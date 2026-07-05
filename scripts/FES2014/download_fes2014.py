#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""
FES2014 Secure FTP Downloader and Extractor
Automates the authenticated download and extraction of FES2014 tidal datasets
from the AVISO+ dissemination servers.
"""

import os
import sys
import ssl
import tarfile
import getpass
from pathlib import Path
from ftplib import FTP_TLS, FTP, error_perm
from dotenv import load_dotenv

def download_file_with_progress(ftp, remote_filepath, local_filepath):
    """Downloads a file from the FTP server showing a progress bar."""
    print(f"Downloading {remote_filepath}...")
    
    try:
        # Get total size for progress tracking
        total_size = ftp.size(remote_filepath)
    except Exception:
        total_size = None

    downloaded = 0

    with open(local_filepath, 'wb') as f:
        def callback(chunk):
            nonlocal downloaded
            f.write(chunk)
            downloaded += len(chunk)
            if total_size:
                percent = (downloaded / total_size) * 100
                sys.stdout.write(f"\r  Progress: {percent:.2f}% ({downloaded / (1024*1024):.1f} MB / {total_size / (1024*1024):.1f} MB)")
                sys.stdout.flush()
            else:
                sys.stdout.write(f"\r  Progress: {downloaded / (1024*1024):.1f} MB downloaded")
                sys.stdout.flush()

        # Download using secure passive mode binary transfer
        ftp.retrbinary(f"RETR {remote_filepath}", callback)
    print("\n  Download complete.")


def extract_tar_xz(archive_path, extract_dir):
    """Decompresses and extracts .tar.xz archive using Python's native LZMA module."""
    print(f"Decompressing and extracting {archive_path} to {extract_dir}...")
    if not os.path.exists(extract_dir):
        os.makedirs(extract_dir)

    # 'r:xz' opens the archive with LZMA2 compression (native in Python 3.3+)
    with tarfile.open(archive_path, 'r:xz') as tar:
        tar.extractall(path=extract_dir)
    print("  Decompression complete.")


def main():
    print("=== AVISO+ FES2014 Automated Downloader ===")
    print("Requires a valid AVISO+ credential. Register at: ")
    print("https://www.aviso.altimetry.fr/en/data/data-access/registration-form.html\n")

    # 1. Collect secure login credentials
    load_dotenv(Path(__file__).parent / '.env')
    username = os.environ.get('AVISO_FTP_USER') or input("AVISO+ FTP Username (Email): ").strip()
    password = os.environ.get('AVISO_FTP_PASSWORD') or getpass.getpass("AVISO+ FTP Password: ").strip()

    if not username or not password:
        print("[Error] Username and Password are required.")
        return

    # 2. Configure paths
    local_target_dir = "./tide_models/fes2014"
    os.makedirs(local_target_dir, exist_ok=True)

    # Standard AVISO+ FTP parameters
    ftp_host = "ftp-access.aviso.altimetry.fr"
    
    # Files to target (Extrapolated tides, Eastward and Northward currents)
    targets = [
        {
            "remote": "/auxiliary/tide_model/fes2014_elevations_and_load/fes2014b_elevations_extrapolated/ocean_tide_extrapolated.tar.xz",
            "local_archive": os.path.join(local_target_dir, "ocean_tide_extrapolated.tar.xz"),
            "extract_to": local_target_dir
        },
        {
            "remote": "/auxiliary/tide_model/fes2014a_currents/eastward_velocity.tar.xz",
            "local_archive": os.path.join(local_target_dir, "eastward_velocity.tar.xz"),
            "extract_to": local_target_dir
        },
        {
            "remote": "/auxiliary/tide_model/fes2014a_currents/northward_velocity.tar.xz",
            "local_archive": os.path.join(local_target_dir, "northward_velocity.tar.xz"),
            "extract_to": local_target_dir
        }
    ]

    # 3. Connect securely and execute
    print(f"\nConnecting securely to {ftp_host}...")

    # Create a permissive SSL context (AVISO certs may not validate cleanly)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    def _ftps_auth(conn, proto='TLS'):
        """Send AUTH command and wrap socket for explicit FTPS."""
        conn.voidcmd(f'AUTH {proto}')
        conn.sock = conn.context.wrap_socket(
            conn.sock, server_hostname=conn.host
        )
        conn.file = conn.sock.makefile(mode='r', encoding=conn.encoding)
        conn.ftp_has_auth = True

    for use_tls in [True, False]:
        if use_tls:
            conn = FTP_TLS(context=ssl_context)
        else:
            conn = FTP()
        try:
            conn.connect(ftp_host, 21)
            if use_tls:
                try:
                    conn.auth()  # sends AUTH TLS
                except error_perm:
                    print("  AUTH TLS rejected, trying AUTH SSL...")
                    _ftps_auth(conn, 'SSL')
                conn.login(username, password)
                conn.prot_p()
            else:
                conn.login(username, password)
            print("Securely authenticated.")
            ftps = conn
            break
        except Exception as e:
            print(f"  {'FTPS' if use_tls else 'FTP'} connection failed: {e}")
            try:
                conn.close()
            except Exception:
                pass
            if not use_tls:
                print("[Error] All connection methods failed.")
                return

    # Download and extract each targeted archive
    for target in targets:
        remote_path = target["remote"]
        local_archive = target["local_archive"]
        extract_dir = target["extract_to"]

        try:
            # Step A: Download
            download_file_with_progress(ftps, remote_path, local_archive)
            
            # Step B: Extract
            extract_tar_xz(local_archive, extract_dir)
            
            # Step C: Clean up the raw download to save disk space
            print(f"Cleaning up raw archive {local_archive}...")
            os.remove(local_archive)
            
        except Exception as e:
            print(f"\n[Error] Processing failed for {remote_path}: {e}")
            print("\n--- Starting interactive FTP explorer ---")
            _explore(ftps)
            break

    # 4. Close secure connection
    try:
        ftps.quit()
    except Exception:
        pass
    print("\n=== Pipeline data population complete ===")


def _explore(conn):
    """Interactive FTP directory browser.

    Commands: <dirname> to enter, '..' to go up, 'pwd', or Enter to quit.
    """
    while True:
        try:
            print(f"\n  {conn.pwd()}")
            items = []
            for name, facts in conn.mlsd():
                typ = facts.get('type', '')
                items.append((name, typ))
            items.sort(key=lambda x: (x[1] != 'dir', x[0].lower()))
            for name, typ in items:
                print(f"    {name}{'/' if typ == 'dir' else ' '}")
        except Exception:
            print("  (MLSD not supported)")
            conn.dir('.', lambda l: print(f"    {l}"))

        cmd = input("\n  > ").strip()
        if not cmd:
            break
        elif cmd == 'pwd':
            print(f"  {conn.pwd()}")
        elif cmd == '..':
            try:
                conn.cwd('..')
            except Exception as e:
                print(f"  {e}")
        else:
            try:
                conn.cwd(cmd)
            except Exception as e:
                print(f"  {e}")


if __name__ == "__main__":
    main()
