"""Government sites often forget to send their intermediate certificate. We fetch it from the certificate's own
'issuer' link (like a browser), but never weaken verification."""
import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID

import ingestion.web as web


def make(subject, issuer_cert=None, issuer_key=None, aia_url=None, ca=False):
    key = ec.generate_private_key(ec.SECP256R1())
    name = lambda cn: x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(name(subject))
               .issuer_name(issuer_cert.subject if issuer_cert else name(subject)).public_key(key.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
               .not_valid_after(now + datetime.timedelta(days=30))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if aia_url:
        builder = builder.add_extension(x509.AuthorityInformationAccess([
            x509.AccessDescription(AuthorityInformationAccessOID.CA_ISSUERS, x509.UniformResourceIdentifier(aia_url))]),
            critical=False)
    cert = builder.sign(issuer_key or key, hashes.SHA256())
    return cert, key


def pem(c):
    return c.public_bytes(serialization.Encoding.PEM).decode()


def test_the_missing_intermediate_is_found_through_the_issuer_link(monkeypatch):
    root, root_key = make("Test Root", ca=True)
    inter, inter_key = make("Test Intermediate", root, root_key, ca=True)           # no link above it: it is the last one needed
    leaf, _ = make("gov.example", inter, inter_key, aia_url="http://ca.example/intermediate.cer")
    asked = []

    def fake_download(url):
        asked.append(url)
        return [inter] if url == "http://ca.example/intermediate.cer" else []
    monkeypatch.setattr(web, "_download_certs", fake_download)
    assert web.missing_intermediates(leaf) == [pem(inter)]
    assert asked == ["http://ca.example/intermediate.cer"]


def test_a_certificate_without_an_issuer_link_gives_nothing(monkeypatch):
    root, _ = make("Standalone", ca=True)
    monkeypatch.setattr(web, "_download_certs", lambda url: (_ for _ in ()).throw(AssertionError("nothing to download")))
    assert web.missing_intermediates(root) == []


def test_issuer_links_to_private_addresses_are_never_followed(monkeypatch):
    import pytest
    with pytest.raises(web.FetchError):
        web._download_certs("http://127.0.0.1/evil.cer")                  # the same private-address guard as for pages


def test_a_certificate_error_is_retried_once_with_the_repaired_chain(monkeypatch):
    import httpx
    calls = []

    def fake_download(url, verify):
        calls.append(verify)
        if len(calls) == 1:
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate")
        return b"%PDF-ok", "application/pdf", url
    monkeypatch.setattr(web, "_download", fake_download)
    monkeypatch.setattr(web, "repaired_context", lambda host: "REPAIRED")
    monkeypatch.setattr(web, "_check_public", lambda url: None)
    assert web.fetch("https://gov.example/a.pdf")[0] == b"%PDF-ok"
    assert calls[1] == "REPAIRED" and len(calls) == 2
    # a non-certificate network problem is NOT retried with the repair path
    calls.clear()
    monkeypatch.setattr(web, "_download", lambda url, verify: (_ for _ in ()).throw(httpx.ConnectError("connection refused")))
    import pytest
    with pytest.raises(web.FetchError):
        web.fetch("https://gov.example/a.pdf")
