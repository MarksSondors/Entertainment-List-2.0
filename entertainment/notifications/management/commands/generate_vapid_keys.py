"""
Management command to generate VAPID keys for Web Push notifications.
"""
from django.core.management.base import BaseCommand
from pywebpush import webpush
import os


class Command(BaseCommand):
    help = 'Generate VAPID keys for Web Push notifications'

    def handle(self, *args, **options):
        try:
            from py_vapid import Vapid
            
            # Generate keys
            vapid = Vapid()
            vapid.generate_keys()
            
            # Get keys
            private_key = vapid.private_key.decode('utf-8')
            public_key = vapid.public_key.decode('utf-8')
            
            self.stdout.write(self.style.SUCCESS('\n' + '='*70))
            self.stdout.write(self.style.SUCCESS('VAPID Keys Generated Successfully!'))
            self.stdout.write(self.style.SUCCESS('='*70 + '\n'))
            
            self.stdout.write(self.style.WARNING('Add these to your .env file:\n'))
            
            self.stdout.write(f'WEBPUSH_VAPID_PRIVATE_KEY={private_key}')
            self.stdout.write(f'WEBPUSH_VAPID_PUBLIC_KEY={public_key}')
            self.stdout.write('WEBPUSH_VAPID_ADMIN_EMAIL=your-email@example.com\n')
            
            self.stdout.write(self.style.SUCCESS('='*70))
            self.stdout.write(self.style.WARNING('\n⚠️  IMPORTANT:'))
            self.stdout.write('1. Copy the keys above to your .env file')
            self.stdout.write('2. Replace "your-email@example.com" with your actual contact email')
            self.stdout.write('3. Keep the private key SECRET - do not commit to version control')
            self.stdout.write('4. Restart your Django server after updating .env\n')
            
        except ImportError:
            self.stdout.write(
                self.style.ERROR(
                    'py-vapid not found. Install it with: pip install pywebpush'
                )
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error generating VAPID keys: {str(e)}')
            )
