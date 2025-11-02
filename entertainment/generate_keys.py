"""
Standalone VAPID key generator.
Run this script to generate keys without Django dependencies.
"""

print("Installing py-vapid if needed...")
import subprocess
import sys

try:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    import base64
except ImportError:
    print("Installing cryptography...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "cryptography"])
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    import base64

print("\n" + "="*70)
print("Generating VAPID Keys...")
print("="*70 + "\n")

# Generate private key
private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())

# Get private key bytes
private_bytes = private_key.private_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)

# Get public key
public_key = private_key.public_key()
public_bytes = public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint
)

# Base64 URL-safe encode
def base64url_encode(data):
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')

private_key_str = base64url_encode(private_bytes)
public_key_str = base64url_encode(public_bytes)

print("✅ VAPID Keys Generated Successfully!\n")
print("="*70)
print("Add these to your .env file:")
print("="*70 + "\n")

print(f"WEBPUSH_VAPID_PRIVATE_KEY={private_key_str}")
print(f"WEBPUSH_VAPID_PUBLIC_KEY={public_key_str}")
print("WEBPUSH_VAPID_ADMIN_EMAIL=admin@entertainment-list.com")

print("\n" + "="*70)
print("⚠️  IMPORTANT:")
print("1. Copy the keys above to your .env file")
print("2. Keep the private key SECRET!")
print("3. Restart your Django server after updating .env")
print("="*70 + "\n")
