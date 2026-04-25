""" Virginia Beach Sheriff's Office â automated roster scraper
Runs via GitHub Actions, outputs data.json """
import requests, json, re, base64, time, os, datetime
from bs4 import BeautifulSoup

BASE      = 'https://inmateinfo.vbso.net:8445/IML'
IMG_BASE  = 'https://inmateinfo.vbso.net:8445/imageservlet'
IMG_HOST  = 'https://inmateinfo.vbso.net:8445'
LETTERS   = list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
RESULT_CAP = 30          # IML silently caps results at this count
OCIS_BASE  = 'https://eapps.courts.state.va.us'
OCIS_API   = OCIS_BASE + '/ocis-rest/api/public/'

# Seed set of known IML placeholder (byte_length, byte_sum) pairs.
# Extended automatically at runtime by scanning for duplicates in existing data.
PLACEHOLDER_IMAGES = set()  # VB: seeded empty, auto-learned at runtime


def _bytes_sig(content):
    """Return (byte_length, byte_sum) signature for image bytes."""
    return (len(content), sum(content) & 0xFFFFFFFF)

def _uri_sig(data_uri):
    """Return (byte_length, byte_sum) for a base64 data URI, or None on error."""
    try:
        b64 = data_uri.split(',', 1)[1]
        content = base64.b64decode(b64)
        return _bytes_sig(content)
    except Exception:
        return None

def is_placeholder_uri(data_uri):
    """Return True if this data URI encodes a known IML placeholder image."""
    if not data_uri or len(data_uri) < 100:
        return False
    sig = _uri_sig(data_uri)
    return sig is not None and sig in PLACEHOLDER_IMAGES

def _image_data(content):
    """Convert raw bytes to a data URI; returns '' for known placeholder images."""
    if len(content) > 500:
        sig = _bytes_sig(content)
        if sig in PLACEHOLDER_IMAGES:
            return ''
        mime = 'image/png' if content[:4] == b'\x89PNG' else 'image/jpeg'
        return f'data:{mime};base64,' + base64.b64encode(content).decode()
    return ''

def search_prefix(sess, prefix):
    """POST a last-name prefix search; returns list of inmate dicts."""
    try:
        r = sess.post(BASE, data={
            'flow_action':                  'searchbyname',
            'quantity':                     '999',
            'systemUser_lastName':          prefix,
            'systemUser_firstName':         '',
            'systemUser_includereleasedinmate':  'Y',
            'systemUser_includereleasedinmate2': 'Y',
            'searchtype': 'name'
        }, timeout=30)
        soup = BeautifulSoup(r.text, 'html.parser')
        result = []
        for tr in soup.find_all('tr', onclick=True):
            m = re.search(r"rowClicked\('(\d+)','(\d+)','(\d+)'\)", tr['onclick'])
            if not m: continue
            cells = [td.get_text(strip=True) for td in tr.find_all('td')]
            if len(cells) < 4: continue
            result.append({
                'sysID':      m.group(2),
                'imgSysID':   m.group(3),
                'name':       cells[0],
                'bookingNum': cells[1],
                'dob':        cells[3],
                'releaseDate':cells[4] if len(cells) > 4 else ''
            })
        return result
    except Exception as e:
        print(f'  Prefix {prefix} error: {e}')
        return []

def scan_prefix(sess, prefix, roster, depth=0):
    """Recursively scan prefix, drilling deeper when result cap is hit."""
    results = search_prefix(sess, prefix)
    if len(results) < RESULT_CAP:
        for inmate in results:
            bn = inmate['bookingNum']
            if bn: roster[bn] = inmate
    else:
        if depth < 3:
            for c in LETTERS:
                scan_prefix(sess, prefix + c, roster, depth + 1)
            time.sleep(0.15)
        else:
            for inmate in results:
                bn = inmate['bookingNum']
                if bn: roster[bn] = inmate

def fetch_mugshot(sess, sysID, imgSysID):
    """Fetch mugshot from imageservlet; 3 attempts with 1s backoff."""
    for attempt in range(3):
        try:
            r = sess.get(f'{IMG_BASE}?sysid={sysID}&imgsysid={imgSysID}', timeout=15)
            if r.ok:
                data = _image_data(r.content)
                if data: return data
        except: pass
        if attempt < 2: time.sleep(1)
    return ''

def fetch_inmate_detail(sess, sysID, imgSysID):
    """Fetch detail page: sex, race, county, commitmentDate, charges, bonds.
    Also returns 'mugshot_img_srcs' (list) if <img> tags pointing to imageservlet is found."""
    try:
        r = sess.post(BASE, data={
            'flow_action': 'edit',
            'sysID':       sysID,
            'imgSysID':    imgSysID,
        }, timeout=30)
        if not r.ok: return {}
        html = r.text
        soup = BeautifulSoup(html, 'html.parser')
        tds  = soup.find_all('td')
        def get_val(label):
            for i, td in enumerate(tds):
                if td.get_text(strip=True) == label and i + 1 < len(tds):
                    return tds[i + 1].get_text(strip=True)
            return ''
        sex             = get_val('Sex:')
        race            = get_val('Race:')
        county          = get_val('County:')
        commitment_date = get_val('Commitment Date:')
        location        = get_val('Current Location:')
        demographics = {
            'dob':              get_val('DOB:'),
            'height':           get_val('Height:'),
            'weight':           get_val('Weight:'),
            'hairColor':        get_val('Hair Color:'),
            'hairLength':       get_val('Hair Length:'),
            'eyeColor':         get_val('Eye Color:'),
            'complexion':       get_val('Complexion:'),
            'ethnicity':        get_val('Ethnicity:'),
            'maritalStatus':    get_val('Marital Status:'),
            'citizen':          get_val('Citizen:'),
            'countryOfBirth':   get_val('Country of Birth:'),
            'permanentId':      get_val('Permanent ID #:'),
            'stateId':          get_val('State ID:'),
            'policeCountyId':   get_val('Police/County ID:'),
            'fbiNum':           get_val('FBI #:'),
            'iceNum':           get_val('ICE #:'),
            'projectedRelease': get_val('Projected Release Date:'),
        }
        mugshot_img_srcs = []
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if 'imageservlet' in src.lower():
                if src.startswith('http'):
                    pass
                elif src.startswith('/'):
                    src = IMG_HOST + src
                else:
                    src = IMG_HOST + '/' + src
                if src not in mugshot_img_srcs:
                    mugshot_img_srcs.append(src)
        bi = html.find('Bond Information')
        ci = html.find('Charge Information')
        bond_soup = BeautifulSoup(
            html[bi:ci] if bi >= 0 and ci > bi else '', 'html.parser'
        )
        bonds = []
        for row in bond_soup.find_all('tr', attrs={
            'bgcolor': lambda v: v and v.upper() in ('#FFFFFF', '#EEEEEE', '#CCCCFF')
        }):
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cells) >= 2 and cells[1]:
                bonds.append({
                    'caseNum':    cells[0],
                    'bondType':   cells[1],
                    'amount':     cells[2] if len(cells) > 2 else '',
                    'status':     cells[3] if len(cells) > 3 else '',
                    'percent':    cells[4] if len(cells) > 4 else '',
                    'setBy':      cells[5] if len(cells) > 5 else '',
                    'additional': cells[6] if len(cells) > 6 else '',
                    'setDate':    cells[7] if len(cells) > 7 else '',
                    'total':      cells[8] if len(cells) > 8 else '',
                })
        charge_soup = BeautifulSoup(html[ci:] if ci >= 0 else '', 'html.parser')
        charges = []
        for row in charge_soup.find_all('tr', attrs={
            'bgcolor': lambda v: v and v.upper() in ('#FFFFFF', '#EEEEEE', '#CCCCFF')
        }):
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cells) >= 4 and any(cells[1:4]):
                charges.append({
                    'offenseDate': cells[1] if len(cells) > 1 else '',
                    'code':        cells[2] if len(cells) > 2 else '',
                    'description': cells[3] if len(cells) > 3 else '',
                    'grade':       cells[4] if len(cells) > 4 else '',
                })
        return {
            'sex':            sex,
            'race':           race,
            'county':         county,
            'commitmentDate': commitment_date,
            'location':       location,
            'charges':        charges,
            'bonds':          bonds,
            'mugshot_img_srcs': mugshot_img_srcs,
            'demographics':     demographics,
        }
    except Exception as e:
        print(f'  Detail error sysID={sysID}: {e}')
        return {}

def fetch_mugshot_from_url(sess, url):
    """Try to download a mugshot directly from a URL (detail-page fallback)."""
    for attempt in range(3):
        try:
            r = sess.get(url, timeout=15)
            if r.ok:
                data = _image_data(r.content)
                if data: return data
        except: pass
        if attempt < 2: time.sleep(1)
    return ''

def init_ocis_session(sess):
    """Accept OCIS 2.0 T&C once to establish a valid session."""
    try:
        sess.get(OCIS_BASE + '/ocis/landing', timeout=15)
        sess.get(OCIS_API  + 'termsAndCondAccepted', timeout=15)
    except Exception as e:
        print(f'  OCIS session init failed: {e}')

def fetch_case_details(sess, row):
    """Fetch defendant DOB, disposition, and sentence from OCIS case detail."""
    empty = {'dob': '', 'disposition': '', 'sentence': ''}
    try:
        detail_payload = {
            'qualifiedFips':       row.get('qualifiedFips', ''),
            'courtLevel':          row.get('courtLevel', ''),
            'divisionType':        row.get('divisionType', ''),
            'caseNumber':          row.get('caseNumber', ''),
            'formattedCaseNumber': row.get('formattedCaseNumber', ''),
            'name':                row.get('name', ''),
            'offenseDate':         row.get('offenseDate', ''),
            'chargeAmended':       row.get('chargeAmended', False),
            'codeSection':         row.get('codeSection', ''),
            'chargeDesc':          row.get('chargeDesc', ''),
            'caseType':            row.get('caseType', ''),
            'hearingDate':         row.get('hearingDate', ''),
        }
        r = sess.post(
            OCIS_API + 'getCaseDetails',
            json=detail_payload,
            timeout=15,
            headers={
                'Content-Type': 'application/json',
                'Referer':      OCIS_BASE + '/ocis/details',
            }
        )
        if not r.ok:
            return empty
        data = r.json()
        payload = (data.get('context', {})
                       .get('entity', {})
                       .get('payload', {}))
        dob = ''
        for p in payload.get('caseParticipant', []):
            if p.get('participantCode') == 'DEF':
                dob = p.get('personalDetails', {}).get('maskedBirthDate', '')
                break
        disp = ''
        sentence = ''
        # Disposition code from dispositionInfo.dispositionText
        disp_obj = payload.get('disposition', {})
        if disp_obj:
            di = disp_obj.get('dispositionInfo', {})
            if di:
                dt = di.get('dispositionText', '')
                if dt:
                    disp = dt
        # Build sentence string from sentencingInformation
        sent_info = payload.get('sentencingInformation', {})
        if sent_info:
            s = sent_info.get('sentence', {})
            parts = []
            if s.get('years'): parts.append(str(s['years']) + 'y')
            if s.get('months'): parts.append(str(s['months']) + 'm')
            if s.get('days'): parts.append(str(s['days']) + 'd')
            if parts:
                sentence = ' '.join(parts)
                susp = sent_info.get('sentenceSuspended', {})
                sp = []
                if susp.get('years'): sp.append(str(susp['years']) + 'y')
                if susp.get('months'): sp.append(str(susp['months']) + 'm')
                if susp.get('days'): sp.append(str(susp['days']) + 'd')
                if sp:
                    sentence += ' (' + ' '.join(sp) + ' susp)'
        return {'dob': dob, 'disposition': disp, 'sentence': sentence}
    except Exception:
        return empty

def fetch_va_court(sess, name, dob):
    """Search OCIS 2.0 statewide for adult criminal/traffic cases by name."""
    try:
        payload = {
            'courtLevels':    [],
            'divisions':      ['Adult Criminal/Traffic'],
            'selectedCourts': [],
            'searchString':   [name.strip()],
            'searchBy':       'N',
        }
        r = sess.post(
            OCIS_API + 'search',
            json=payload,
            timeout=20,
            headers={
                'Content-Type': 'application/json',
                'Referer':      OCIS_BASE + '/ocis/search',
            }
        )
        if not r.ok: return []
        data = r.json()
        results = (data.get('context', {})
                       .get('entity', {})
                       .get('payload', {})
                       .get('searchResults', []))
        cases = []
        for row in results:
            details = fetch_case_details(sess, row)
            cases.append({
                'formattedCaseNum':  row.get('formattedCaseNumber', ''),
                'caseTrackingID':    row.get('caseNumber', ''),
                'court':             row.get('qualifiedFips', ''),
                'courtLevel':        row.get('courtLevel', ''),
                'offenseDate':       row.get('offenseDate', ''),
                'codeSection':       row.get('codeSection', ''),
                'chargeDesc':        row.get('chargeDesc', ''),
                'dispositionDate':   row.get('hearingDate', ''),
                'dispositionDesc':   details.get('disposition', ''),
                'sentence':          details.get('sentence', ''),
                'defendantName':      row.get('name', ''),
                'defendantDOB':       details.get('dob', ''),
            })
            time.sleep(0.15)
        return cases
    except Exception as e:
        print(f'  OCIS error for {name}: {e}')
        return []

def auto_detect_placeholders(prev):
    """Scan existing mugshots for duplicates across DIFFERENT people.
    If the same image bytes appear in records belonging to 2+ distinct
    (name, dob) pairs, it must be an IML placeholder â no two different
    people would legitimately share an identical mugshot.
    (Same photo across multiple bookings of the same person is fine.)
    Extends the global PLACEHOLDER_IMAGES set in-place."""
    sig_to_people = {}
    for rec in prev.values():
        mug = rec.get('mugshot', '')
        if not mug or len(mug) < 100:
            continue
        sig = _uri_sig(mug)
        if sig is None:
            continue
        person_key = (rec.get('name', '').upper().strip(), rec.get('dob', ''))
        if sig not in sig_to_people:
            sig_to_people[sig] = set()
        sig_to_people[sig].add(person_key)
    new_found = 0
    new_records = 0
    for sig, people in sig_to_people.items():
        if len(people) >= 2 and sig not in PLACEHOLDER_IMAGES:
            PLACEHOLDER_IMAGES.add(sig)
            new_found += 1
            new_records += len(people)
    if new_found:
        print(f'Auto-detected {new_found} new placeholder variants ({new_records} records affected)')

def main():
    sess = requests.Session()
    sess.headers['User-Agent'] = 'Mozilla/5.0'
    init_ocis_session(sess)

    court_by_bn = {}
    if os.path.exists('court_data.json'):
        with open('court_data.json') as f:
            court_by_bn = json.load(f)

    prev = {}
    if os.path.exists('data.json'):
        with open('data.json') as f:
            for rec in json.load(f):
                prev[rec['bookingNum']] = rec

    # Auto-extend PLACEHOLDER_IMAGES from any duplicates in existing data
    print(f'Scanning {len(prev)} existing records for placeholder patterns...')
    auto_detect_placeholders(prev)
    print(f'Total known placeholder variants: {len(PLACEHOLDER_IMAGES)}')

    court_by_name_dob = {}
    for bn, cases in court_by_bn.items():
        p = prev.get(bn, {})
        n, d = p.get('name', ''), p.get('dob', '')
        if n and d and cases:
            court_by_name_dob[n.upper().strip() + '|' + d] = cases

    roster = {}
    for letter in LETTERS:
        print(f'Scanning {letter}...')
        scan_prefix(sess, letter, roster)
        time.sleep(0.3)
    print(f'Roster: {len(roster)} inmates')

    # Safety: if roster is unexpectedly empty but we had prior data, IML is likely down.
    # Abort without overwriting data.json to prevent data loss.
    if len(roster) == 0 and len(prev) > 0:
        print(f'WARNING: Roster is empty but {len(prev)} records existed â IML server may be down, aborting.')
        import sys; sys.exit(0)

    records = []
    items = sorted(roster.items(), key=lambda x: x[0], reverse=True)
    new_court = dict(court_by_bn)

    for i, (bn, inmate) in enumerate(items):
        if i % 50 == 0: print(f'Processing {i}/{len(items)}...')

        ex = prev.get(bn, {})

        # Re-fetch if no mugshot or if cached mugshot is a known placeholder
        cached_mug   = ex.get('mugshot', '')
        need_mugshot = not cached_mug or is_placeholder_uri(cached_mug)
        curr_yr_pfx  = '{:02d}-'.format(datetime.date.today().year % 100)
        need_detail  = (not ex.get('sex') or
                        ex.get('charges') is None or
                        (ex.get('charges') == [] and bn[:3] == curr_yr_pfx))

        if need_mugshot or need_detail:
            if need_mugshot:
                mugshot = fetch_mugshot(sess, inmate['sysID'], inmate['imgSysID'])
            else:
                mugshot = ex['mugshot']

            if need_detail:
                detail = fetch_inmate_detail(sess, inmate['sysID'], inmate['imgSysID'])
                time.sleep(0.2)
            else:
                detail = {
                    'sex':      ex.get('sex', ''),    'race':           ex.get('race', ''),
                    'county':   ex.get('county', ''), 'location':       ex.get('location', ''),
                    'commitmentDate': ex.get('commitmentDate', ''),
                    'charges':  ex.get('charges', []), 'bonds':         ex.get('bonds', []),
                    'mugshot_img_srcs': [],
                }

            if not mugshot:
                for _src in detail.get('mugshot_img_srcs', []):
                    mugshot = fetch_mugshot_from_url(sess, _src)
                    if mugshot:
                        print(f'  Mugshot via detail fallback: {inmate["name"]}')
                        break

            if not mugshot:
                mugshot = ''   # never store a placeholder
        else:
            # Existing complete record with a real photo â skip HTTP fetches
            mugshot = ex['mugshot']
            detail  = {
                'sex':      ex.get('sex', ''),    'race':           ex.get('race', ''),
                'county':   ex.get('county', ''), 'location':       ex.get('location', ''),
                'commitmentDate': ex.get('commitmentDate', ''),
                'charges':  ex.get('charges', []), 'bonds':         ex.get('bonds', []),
            }

        name_dob_key = inmate['name'].upper().strip() + '|' + inmate['dob']
        court_hist = (
            court_by_bn.get(bn)
            or court_by_name_dob.get(name_dob_key)
            or ex.get('courtHistory', [])
        )
        if not court_hist:
            court_hist = fetch_va_court(sess, inmate['name'], inmate['dob'])
            if court_hist:
                new_court[bn] = court_hist
                print(f'  VA court: {inmate["name"]} â {len(court_hist)} cases')
            time.sleep(0.3)

        records.append({
            'bookingNum':     bn,
            'name':           inmate['name'],
            'dob':            inmate['dob'],
            'sex':            detail.get('sex')            or ex.get('sex', ''),
            'race':           detail.get('race')           or ex.get('race', ''),
            'location':       detail.get('location')       or ex.get('location', ''),
            'county':         detail.get('county')         or ex.get('county', ''),
            'commitmentDate': detail.get('commitmentDate') or ex.get('commitmentDate', ''),
            'releaseDate':    inmate['releaseDate'],
            'charges':        detail.get('charges')        or ex.get('charges', []),
            'bonds':          detail.get('bonds')          or ex.get('bonds', []),
            'mugshot':        mugshot,
            'courtHistory':   court_hist,
            'demographics':   detail.get('demographics', {}),
        })
        time.sleep(0.15)

    if new_court != court_by_bn:
        with open('court_data.json', 'w') as f:
            json.dump(new_court, f, separators=(',', ':'))
        print(f'Updated court_data.json ({len(new_court)} entries)')

    # Carry over records from prev not in current roster (released/expired from IML)
    live_bns = {r['bookingNum'] for r in records}
    today_str = datetime.date.today().strftime('%m/%d/%Y')
    carried = 0
    for bn, ex in prev.items():
        if bn not in live_bns:
            if not ex.get('releaseDate'):
                ex['releaseDate'] = today_str
            records.append(ex)
            carried += 1
    print(f'Carried over {carried} released/dropped records')

    # Overlay firstSeenAt from first_seen.json companion file, if present.
    # This lets us backfill firstSeenAt without pushing 78MB data.json through
    # the Git Blob API (which has a practical request-body size limit).
    if os.path.exists('first_seen.json'):
        try:
            with open('first_seen.json') as _fsfp:
                _first_seen_map = json.load(_fsfp)
            _overlaid = 0
            for _bn, _rec in prev.items():
                if _bn in _first_seen_map:
                    _rec['firstSeenAt'] = _first_seen_map[_bn]
                    _overlaid += 1
            print(f'Overlaid firstSeenAt from first_seen.json onto {_overlaid} records')
        except Exception as _fse:
            print(f'Warning: could not apply first_seen.json overlay - {_fse}')

    # Stamp firstSeenAt (preserves on re-scrape, stamps new). See SORT_RULE.md.
    from first_seen import stamp_all
    records = stamp_all(records, list(prev.values()), key='bookingNum')

    with open('data.json', 'w') as f:
        json.dump(records, f, separators=(',', ':'))
    print(f'Saved {len(records)} records to data.json')
    recent = sorted(records, key=lambda r: r['bookingNum'], reverse=True)[:100]
    with open('recent.json', 'w') as f:
        json.dump(recent, f, separators=(',', ':'))
    print(f'Saved {len(recent)} recent records to recent.json')
    latest = recent[:1]
    with open('latest.json', 'w') as f:
        json.dump(latest, f, separators=(',', ':'))
    print(f'Saved latest record to latest.json')

if __name__ == '__main__':
    main()
