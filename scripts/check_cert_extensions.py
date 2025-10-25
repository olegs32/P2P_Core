#!/usr/bin/env python3
"""
Скрипт для проверки X.509 расширений в сертификатах
"""
from cryptography import x509
from cryptography.hazmat.backends import default_backend
import sys
from pathlib import Path

def check_certificate(cert_file):
    print(f"\n{'='*60}")
    print(f"Checking: {cert_file}")
    print('='*60)

    try:
        cert_path = Path(cert_file)
        if not cert_path.exists():
            print(f"❌ File not found: {cert_file}")
            return False

        with open(cert_file, 'rb') as f:
            cert = x509.load_pem_x509_certificate(f.read(), default_backend())

        print(f"Subject: {cert.subject.rfc4514_string()}")
        print(f"Issuer: {cert.issuer.rfc4514_string()}")
        print(f"Valid from: {cert.not_valid_before}")
        print(f"Valid to: {cert.not_valid_after}")
        print("\nX.509 Extensions:")

        has_aki = False
        has_ski = False
        has_bc = False

        for ext in cert.extensions:
            ext_name = ext.oid._name
            critical = "CRITICAL" if ext.critical else "non-critical"
            print(f"  • {ext_name} ({critical})")

            if ext_name == 'authorityKeyIdentifier':
                has_aki = True
                print("    ✅ Authority Key Identifier PRESENT")
            elif ext_name == 'subjectKeyIdentifier':
                has_ski = True
                print("    ✅ Subject Key Identifier PRESENT")
            elif ext_name == 'basicConstraints':
                has_bc = True
                bc = ext.value
                print(f"    ✅ CA={bc.ca}, path_length={bc.path_length}")

        print("\nValidation:")
        if not has_aki:
            print("  ❌ MISSING: Authority Key Identifier")
            return False
        if not has_ski:
            print("  ⚠️  MISSING: Subject Key Identifier (optional but recommended)")
        if has_bc:
            print("  ✅ Basic Constraints present")

        print("  ✅ Certificate has required extensions")
        return True

    except Exception as e:
        print(f"❌ Error reading certificate: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("="*60)
    print("Certificate X.509 Extensions Checker")
    print("="*60)

    if len(sys.argv) < 2:
        # Default: check certificates in certs/ directory
        cert_files = [
            "certs/ca_cert.cer",
            "certs/coordinator_cert.cer",
            "certs/worker_cert.cer"
        ]
        print("No arguments provided, checking default certificates:")
        for f in cert_files:
            print(f"  - {f}")
    else:
        cert_files = sys.argv[1:]

    results = {}
    for cert_file in cert_files:
        results[cert_file] = check_certificate(cert_file)

    print("\n" + "="*60)
    print("Summary:")
    print("="*60)
    for cert_file, result in results.items():
        status = "✅ OK" if result else "❌ FAILED"
        print(f"{status} - {cert_file}")

    all_ok = all(results.values())
    if all_ok:
        print("\n✅ All certificates have required extensions!")
        return 0
    else:
        print("\n❌ Some certificates are missing required extensions!")
        print("\nTo fix: regenerate certificates with:")
        print("  ./scripts/cleanup_old_certs.sh")
        print("  ./scripts/generate_ca_certs.sh")
        return 1

if __name__ == "__main__":
    sys.exit(main())
