"""Download only UCI's official archive. Never fall back to fabricated data."""
from pathlib import Path
import argparse, hashlib, json, shutil, zipfile
from datetime import datetime, timezone
import requests
ROOT = Path(__file__).resolve().parents[1]
URL = 'https://archive.ics.uci.edu/static/public/352/online+retail.zip'
META = 'https://archive.ics.uci.edu/api/dataset?id=352'
PAGE = 'https://archive.ics.uci.edu/dataset/352/online+retail'

def sha256(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--local-zip', type=Path, help='Archive manually downloaded from UCI')
    args=parser.parse_args()
    raw=ROOT/'data/raw'; raw.mkdir(parents=True, exist_ok=True)
    archive=raw/'online_retail_uci.zip'
    if args.local_zip:
        if args.local_zip.resolve()!=archive.resolve(): shutil.copy2(args.local_zip, archive)
        method='user-supplied archive; origin must be checked by user'
        headers={}
    elif archive.exists():
        method='reuse existing archive; see previous manifest for provenance'
        headers={}
    else:
        temp=archive.with_suffix('.download')
        with requests.get(URL, stream=True, timeout=(20,180)) as response:
            response.raise_for_status(); headers={k:response.headers.get(k) for k in ['ETag','Last-Modified','Content-Length']}
            with temp.open('wb') as f:
                for chunk in response.iter_content(1024*1024): f.write(chunk)
        if not zipfile.is_zipfile(temp):
            temp.unlink(missing_ok=True); raise ValueError('Official response is not a ZIP archive')
        temp.replace(archive); method='downloaded from official HTTPS endpoint'
    with zipfile.ZipFile(archive) as z:
        choices=[n for n in z.namelist() if Path(n).name=='Online Retail.xlsx']
        if len(choices)!=1: raise ValueError('Expected exactly one Online Retail.xlsx')
        target=raw/'Online Retail.xlsx'
        with z.open(choices[0]) as source, target.open('wb') as dest: shutil.copyfileobj(source,dest)
    manifest={'dataset':'UCI Online Retail','dataset_id':352,'source_page':PAGE,'doi':'10.24432/C5BW33',
      'download_url':URL,'recorded_utc':datetime.now(timezone.utc).isoformat(),'method':method,
      'archive_sha256':sha256(archive),'xlsx_sha256':sha256(target),'archive_bytes':archive.stat().st_size,
      'http_headers':headers,'checksum_note':'Locally calculated digest detects later changes; it is not a UCI-published signature.'}
    old=raw/'manifest.json'
    if old.exists():
        previous=json.loads(old.read_text(encoding='utf-8'))
        if previous.get('archive_sha256')==manifest['archive_sha256']:
            manifest['first_recorded_utc']=previous.get('first_recorded_utc',previous.get('recorded_utc'))
            manifest['first_method']=previous.get('first_method',previous.get('method'))
    try:
        response=requests.get(META,timeout=(10,30)); response.raise_for_status()
        (raw/'uci_metadata.json').write_text(response.text,encoding='utf-8')
        manifest['metadata_downloaded']=True
    except requests.RequestException as exc:
        manifest['metadata_downloaded']=False; manifest['metadata_error']=str(exc)
    old.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
