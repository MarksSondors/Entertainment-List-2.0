import os
import requests
import zipfile
import shutil
from django.core.management.base import BaseCommand
from django.conf import settings

class Command(BaseCommand):
    help = 'Downloads and extracts the MovieLens 32M dataset'

    def handle(self, *args, **options):
        url = "https://files.grouplens.org/datasets/movielens/ml-32m.zip"
        data_dir = os.path.join(settings.BASE_DIR, 'data')
        target_dir = os.path.join(data_dir, 'ml-32m')
        zip_path = os.path.join(data_dir, 'ml-32m.zip')

        if not os.path.exists(data_dir):
            os.makedirs(data_dir)

        if os.path.exists(target_dir):
            self.stdout.write(self.style.WARNING(f"Directory {target_dir} already exists."))
            # Check if files are there
            required_files = ['ratings.csv', 'links.csv']
            missing = [f for f in required_files if not os.path.exists(os.path.join(target_dir, f))]
            if not missing:
                self.stdout.write(self.style.SUCCESS("All required files present. Skipping download."))
                return
            else:
                 self.stdout.write(self.style.WARNING(f"Missing files: {missing}. Redownloading..."))
        
        self.stdout.write(f"Downloading {url}...")
        
        try:
            with requests.get(url, stream=True) as r:
                r.raise_for_status()
                total_length = int(r.headers.get('content-length', 0))
                
                with open(zip_path, 'wb') as f:
                    downloaded = 0
                    for chunk in r.iter_content(chunk_size=8192): 
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_length:
                             percent = int(100 * downloaded / total_length)
                             if percent % 5 == 0: # Print every 5%
                                self.stdout.write(f"\rDownloading... {percent}% ({downloaded//1024//1024}MB / {total_length//1024//1024}MB)", ending='')
            
            self.stdout.write("\nDownload complete. Extracting...")
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(data_dir)
                
            self.stdout.write("Extraction complete.")
            
            # Clean up zip file
            if os.path.exists(zip_path):
                os.remove(zip_path)
                self.stdout.write("Cleaned up zip file.")
            
            self.stdout.write(self.style.SUCCESS(f"Dataset ready at {target_dir}"))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
            if os.path.exists(zip_path):
                os.remove(zip_path)
