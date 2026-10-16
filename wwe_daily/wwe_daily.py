"""
WWE daily job. Runs in order:

  1. Upload16      - validate, zip and SFTP the latest YYYYMMDD folder in S:\\23704\\SHIP
  2. Morning setup - rotate/download vendor + PO lists, run the FoxPro morning update, close FoxPro
  3. Folder moves  - pick up today's YYYYMMDD_RAW / _PROCESSED (/ _EXCEPTION) folders from
                     AEG_emails and file them into QC and Processed

Credentials live in credentials.ini and recipients in email_lists.json, both next to this
script (or next to the .exe once frozen with PyInstaller).

Usage:
    wwe_daily.py                    normal run, today's date
    wwe_daily.py --date 20261016    process a specific YYYYMMDD in step 3
    wwe_daily.py --no-pause         don't wait for Enter at the end (Task Scheduler)
    wwe_daily.py --protect-config   restrict credentials.ini to the current Windows user
"""
import argparse
import configparser
import fnmatch
import json
import os
import re
import shutil
import smtplib
import subprocess
import sys
import time
from csv import writer
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import paramiko

# --- Paths / hosts (not secret) ---------------------------------------------------------------
if getattr(sys, "frozen", False):
    CONFIG_DIR = Path(sys.executable).parent          # PyInstaller exe
else:
    CONFIG_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("WWE_CONFIG_DIR", CONFIG_DIR))
CREDENTIALS_FILE = CONFIG_DIR / "credentials.ini"
EMAIL_LISTS_FILE = CONFIG_DIR / "email_lists.json"

# Step 1: upload
ONBASE_HOST = "MFT01.onbaseonline.com"
ONBASE_PORT = 22
LOCAL_BASE_DIR = r"S:\23704\SHIP"
REMOTE_DEST_DIR = "/Incoming"
LOG_FILE = r"S:\23704\SHIP\record.csv"
DELIVERY_CSV_DIR = r"S:\23704\Delivery_CSV"
# If the upload fails, still run the morning setup (the failure email has already gone out).
CONTINUE_AFTER_UPLOAD_FAILURE = True

# Step 2: morning setup
FILES_HOST = "scanningamerica.files.com"
FILES_PORT = 22
FILES_REMOTE_PATH = "/WWE/"
FILES_LOCAL_DIR = r"S:\23704\data\client_data"
FILE_CONFIGS = [
    {"pattern": "SA_F0401VendorMaster*", "final_name": "WWE_Vendors.csv"},
    {"pattern": "SA_F4301PurchaseOrderHeader*", "final_name": "WWE_PO.csv"},
]
FOXPRO_EXE = r"C:\Program Files (x86)\Microsoft Visual Studio\Vfp98\VFP6.EXE"
FOXPRO_PRG = r"S:\23704\data\tim\morning_update.prg"
# FoxPro is waited on until it exits by itself (e.g. the .prg ends with QUIT). If it is still
# open after this many seconds it is terminated.
FOXPRO_MAX_WAIT_SECONDS = 900

# Step 3: folder moves
AEG_EMAILS_DIR = r"S:\23704\$FTP\AEG_emails"
QC_DIR = r"S:\23704\QC"
PROCESSED_DIR = r"S:\23704\Processed"

# Email
SMTP_SERVER = "mail.smtp2go.com"
SMTP_PORT = 2525
EMAIL_FROM = "WWE-Daily@scanningamerica.com"


# --- Config -----------------------------------------------------------------------------------
class ConfigError(Exception):
    pass


class StopProcess(Exception):
    """A condition the user has to deal with. Message is shown and emailed."""


@dataclass
class Config:
    onbase_user: str
    onbase_pass: str
    files_user: str
    files_pass: str
    smtp_user: str
    smtp_pass: str
    recipients_success: list
    recipients_failure: list


def load_config():
    for f in (CREDENTIALS_FILE, EMAIL_LISTS_FILE):
        if not f.is_file():
            raise ConfigError(f"Missing config file: {f}")

    # interpolation=None so passwords containing '%' are read literally
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(CREDENTIALS_FILE, encoding="utf-8")
    wanted = {
        "onbase_user": ("onbase_sftp", "user"), "onbase_pass": ("onbase_sftp", "password"),
        "files_user": ("files_sftp", "user"), "files_pass": ("files_sftp", "password"),
        "smtp_user": ("smtp", "user"), "smtp_pass": ("smtp", "password"),
    }
    values, missing = {}, []
    for key, (section, option) in wanted.items():
        val = cp.get(section, option, fallback="").strip()
        if not val:
            missing.append(f"[{section}] {option}")
        values[key] = val
    if missing:
        raise ConfigError(f"Empty or missing in {CREDENTIALS_FILE.name}: {', '.join(missing)}")

    try:
        lists = json.loads(EMAIL_LISTS_FILE.read_text(encoding="utf-8"))
        success = [a.strip() for a in lists.get("success", []) if a.strip()]
        failure = [a.strip() for a in lists.get("failure", []) if a.strip()]
    except (ValueError, AttributeError) as e:
        raise ConfigError(f"Could not parse {EMAIL_LISTS_FILE.name}: {e}")

    return Config(recipients_success=success, recipients_failure=failure, **values)


def protect_config_file():
    """Windows only: remove inherited permissions and leave the current user as the only one with access."""
    if os.name != "nt":
        raise SystemExit("--protect-config only works on Windows (uses icacls).")
    if not CREDENTIALS_FILE.is_file():
        raise SystemExit(f"{CREDENTIALS_FILE} not found.")
    user = os.environ.get("USERNAME")
    subprocess.run(["icacls", str(CREDENTIALS_FILE), "/inheritance:r", "/grant:r", f"{user}:F"], check=True)
    print(f"{CREDENTIALS_FILE.name} now accessible only by {user}. "
          f"A scheduled task must run as this same user to read it.")


# --- Email ------------------------------------------------------------------------------------
def send_email(cfg, recipients, subject, body, attachment=None):
    if not recipients:
        print(f"No recipients configured, email not sent: {subject}")
        return
    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)

    if attachment and os.path.exists(attachment):
        try:
            with open(attachment, "rb") as f:
                msg.add_attachment(f.read(), maintype="text", subtype="csv",
                                   filename=os.path.basename(attachment))
        except Exception as e:
            print(f"Attachment error: {e}")

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(cfg.smtp_user, cfg.smtp_pass)
            server.send_message(msg)
        print(f"Email sent: {subject}")
    except Exception as e:
        print(f"Error sending email '{subject}': {e}")


def notify_failure(cfg, subject, body):
    send_email(cfg, cfg.recipients_failure, subject, body)


# --- Step 1: Upload ---------------------------------------------------------------------------
def get_latest_folder():
    base_path = Path(LOCAL_BASE_DIR)
    date_folders = [
        d for d in base_path.iterdir()
        if d.is_dir() and d.name.isdigit() and len(d.name) == 8
    ]
    if not date_folders:
        print("Error: No YYYYMMDD folders found in base directory.")
        return None
    return max(date_folders, key=lambda x: x.name)


def validate_and_zip(folder_path):
    tif_count = len(list(folder_path.glob("*.tif")))
    txt_count = len(list(folder_path.glob("*.txt")))

    if tif_count != txt_count:
        raise ValueError(f"CRITICAL ERROR: Count mismatch in {folder_path.name}. "
                         f"(TIF: {tif_count}, TXT: {txt_count}). Upload aborted.")
    if tif_count == 0:
        raise ValueError(f"ERROR: Folder {folder_path.name} is empty or contains no TIF/TXT files.")

    zip_name = f"{folder_path.name}.zip"
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            if zip_name in f.read():
                raise FileExistsError(f"SKIP: {zip_name} has already been recorded as uploaded in the CSV.")

    zip_path = folder_path / zip_name
    print(f"Counts match ({tif_count}). Zipping inside {folder_path.name}...")
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zipf:
        for file in folder_path.iterdir():
            if file.name != zip_name:
                zipf.write(file, arcname=file.name)

    return zip_path, tif_count, txt_count


def upload_via_sftp(cfg, file_path, tif_count, txt_count):
    """Returns the status string: 'Success' or 'Fail: <reason>'."""
    max_retries = 3
    retry_delay = 30
    status_msg = "Fail: Process did not complete"

    for attempt in range(max_retries):
        transport = None
        try:
            print(f"Attempt {attempt + 1} of {max_retries}...")
            transport = paramiko.Transport((ONBASE_HOST, ONBASE_PORT))
            transport.banner_timeout = 30
            transport.connect(username=cfg.onbase_user, password=cfg.onbase_pass)

            sftp = paramiko.SFTPClient.from_transport(transport)
            sftp.chdir(REMOTE_DEST_DIR)
            sftp.put(str(file_path), file_path.name)

            status_msg = "Success"
            print("Upload Successful.")
            break
        except Exception as e:
            status_msg = f"Fail: {e}"
            print(f"Error on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                print(f"Waiting {retry_delay}s to retry...")
                time.sleep(retry_delay)
            else:
                print("All attempts exhausted.")
        finally:
            if transport:
                transport.close()

    update_record(file_path.name, status_msg, tif_count, txt_count)
    send_upload_notification(cfg, file_path.name, status_msg, tif_count, txt_count)
    return status_msg


def update_record(filename, status, tif_count, txt_count):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > 0
    with open(LOG_FILE, "a", newline="") as f:
        csv_writer = writer(f)
        if not file_exists:
            csv_writer.writerow(["Filename", "Date Uploaded", "Status/Error", "Tif Count", "Txt Count"])
        csv_writer.writerow([filename, timestamp, status, tif_count, txt_count])


def send_upload_notification(cfg, filename, status, tif_count, txt_count):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if "Success" in status:
        subject = f"WWE Invoices Uploaded - {filename} (SUCCESS)"
        body = f"""
        The scheduled WWE upload for {filename} completed successfully.

        Details:
        - TIFs Processed: {tif_count}
        - TXTs Processed: {txt_count}
        - Completion Time: {now}

        Attached is the matching delivery CSV for your records.

        This is an automated message.
        """
        csv_path = os.path.join(DELIVERY_CSV_DIR, filename.replace(".zip", ".csv"))
        send_email(cfg, cfg.recipients_success, subject, body, attachment=csv_path)
    else:
        subject = f"URGENT: WWE Upload Failed - {filename}"
        body = f"""
        *** ATTENTION: WWE UPLOAD FAILED ***

        The upload process for {filename} encountered an error and could not complete.

        ERROR DETAILS:
        {status}

        Timestamp of failure: {now}
        """
        send_email(cfg, cfg.recipients_failure, subject, body)


def run_upload(cfg):
    """Returns True if the upload succeeded or there was nothing to upload, False on failure."""
    try:
        target_folder = get_latest_folder()
        if not target_folder:
            return False
        zip_to_send, tifs, txts = validate_and_zip(target_folder)
        return upload_via_sftp(cfg, zip_to_send, tifs, txts) == "Success"
    except (ValueError, FileExistsError) as e:
        print(e)
        return False
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return False


# --- Step 2: Morning setup --------------------------------------------------------------------
def rotate_and_download(cfg):
    transport = paramiko.Transport((FILES_HOST, FILES_PORT))
    transport.connect(username=cfg.files_user, password=cfg.files_pass)
    sftp = paramiko.SFTPClient.from_transport(transport)

    try:
        remote_files = sftp.listdir(FILES_REMOTE_PATH)

        for config in FILE_CONFIGS:
            local_active_path = os.path.join(FILES_LOCAL_DIR, config["final_name"])

            # 1. Archive the old file using its 'Date Modified'
            if os.path.exists(local_active_path):
                mod_date = datetime.fromtimestamp(os.path.getmtime(local_active_path)).strftime("%m%d%y")
                base_name = os.path.splitext(config["final_name"])[0]
                archive_name = f"{base_name} - {mod_date}.csv"
                os.rename(local_active_path, os.path.join(FILES_LOCAL_DIR, archive_name))
                print(f"Archived: {config['final_name']} -> {archive_name}")

            # 2. Find and download the newest matching remote file
            matches = fnmatch.filter(remote_files, config["pattern"])
            if matches:
                matches.sort(reverse=True)
                target_remote = matches[0]
                sftp.get(FILES_REMOTE_PATH + target_remote, local_active_path)
                print(f"Downloaded: {target_remote} as {config['final_name']}")
            else:
                print(f"Notice: No remote file found for {config['pattern']}")
    finally:
        sftp.close()
        transport.close()


def run_foxpro_and_close():
    print(f"Vendor and PO lists downloaded. Running {os.path.basename(FOXPRO_PRG)}")
    proc = subprocess.Popen([FOXPRO_EXE, "-T", FOXPRO_PRG])
    try:
        proc.wait(timeout=FOXPRO_MAX_WAIT_SECONDS)
        print("FoxPro exited on its own.")
    except subprocess.TimeoutExpired:
        print(f"FoxPro still open after {FOXPRO_MAX_WAIT_SECONDS}s, closing it.")
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    print("Setup complete. Please go through Combines Process.")


def run_morning_setup(cfg):
    rotate_and_download(cfg)
    run_foxpro_and_close()


# --- Step 3: Folder moves ---------------------------------------------------------------------
QC_NAME_RE = re.compile(r"^(\d{4})_qc$", re.IGNORECASE)


def find_items(source, date_str, kind):
    prefix = f"{date_str}_{kind}".upper()
    return [p for p in source.iterdir() if p.name.upper().startswith(prefix)]


def next_qc_name(qc_dir):
    """Take the most recently modified NNNN_qc folder, add one to NNNN."""
    candidates = [p for p in qc_dir.iterdir() if p.is_dir() and QC_NAME_RE.match(p.name)]
    if not candidates:
        raise StopProcess(f"No existing NNNN_qc folders in {qc_dir}, can't work out the next number.")
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    number = int(QC_NAME_RE.match(latest.name).group(1))
    return f"{number + 1:04d}_qc", latest.name


def organize_daily_folders(date_str, source=AEG_EMAILS_DIR, qc_dir=QC_DIR, processed_dir=PROCESSED_DIR):
    """Raises StopProcess when the user needs to step in. Returns a summary string on success."""
    source, qc_dir, processed_dir = Path(source), Path(qc_dir), Path(processed_dir)

    exceptions = find_items(source, date_str, "EXCEPTION")
    if exceptions:
        raise StopProcess(
            f"EXCEPTION folder found for {date_str}: {', '.join(p.name for p in exceptions)}\n"
            f"Nothing was moved. Please go check it in {source}.")

    found = {kind: find_items(source, date_str, kind) for kind in ("RAW", "PROCESSED")}
    missing = [f"{date_str}_{kind}" for kind, items in found.items() if not items]
    if missing:
        raise StopProcess(f"Missing in {source}: {', '.join(missing)}\n"
                          f"Please upload the files, then run again.")
    for kind, items in found.items():
        if len(items) > 1:
            raise StopProcess(f"More than one {date_str}_{kind} match in {source}: "
                              f"{', '.join(p.name for p in items)}. Nothing was moved.")

    raw, processed = found["RAW"][0], found["PROCESSED"][0]
    new_qc_name, previous_qc = next_qc_name(qc_dir)
    qc_dest = qc_dir / new_qc_name
    raw_dest = processed_dir / raw.name
    for dest in (qc_dest, raw_dest):
        if dest.exists():
            raise StopProcess(f"Destination already exists, nothing was moved: {dest}")

    # Both destinations checked up front so a failure can't leave a half-finished move
    shutil.move(str(processed), str(qc_dest))
    shutil.move(str(raw), str(raw_dest))
    return (f"{processed.name} -> {qc_dest} (previous QC folder: {previous_qc})\n"
            f"{raw.name} -> {raw_dest}")


# --- Main -------------------------------------------------------------------------------------
def main(date_str):
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}")
        return 1

    print("=== Step 1: Upload ===")
    if not run_upload(cfg) and not CONTINUE_AFTER_UPLOAD_FAILURE:
        print("Upload did not complete, stopping.")
        return 1

    print("\n=== Step 2: Morning setup ===")
    try:
        run_morning_setup(cfg)
    except Exception as e:
        print(f"Morning setup failed: {e}")
        notify_failure(cfg, "URGENT: WWE Morning Setup Failed",
                       f"The morning setup failed and the job stopped.\n\nError: {e}\n"
                       f"Time: {datetime.now():%Y-%m-%d %H:%M:%S}")
        return 1

    print(f"\n=== Step 3: Move {date_str} folders ===")
    try:
        summary = organize_daily_folders(date_str)
    except StopProcess as e:
        print(f"\nSTOPPED: {e}")
        notify_failure(cfg, f"WWE Daily - action needed ({date_str})", str(e))
        return 1
    except Exception as e:
        print(f"Folder move failed: {e}")
        notify_failure(cfg, f"URGENT: WWE folder move failed ({date_str})",
                       f"{e}\n\nCheck {AEG_EMAILS_DIR}, {QC_DIR} and {PROCESSED_DIR} - "
                       f"a move may have partly completed.")
        return 1
    print(summary)
    print("\nAll steps complete.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                        help="YYYYMMDD prefix of the folders to move in step 3 (default: today)")
    parser.add_argument("--no-pause", action="store_true", help="don't wait for Enter at the end")
    parser.add_argument("--protect-config", action="store_true",
                        help="restrict credentials.ini to the current Windows user and exit")
    args = parser.parse_args()

    if args.protect_config:
        protect_config_file()
        sys.exit(0)
    if not re.fullmatch(r"\d{8}", args.date):
        sys.exit("--date must be YYYYMMDD")

    code = main(args.date)
    if not args.no_pause:
        input("\nExecution finished. Press Enter to close this window...")
    sys.exit(code)
