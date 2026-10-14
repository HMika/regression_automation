
#!/usr/bin/env python3
"""
csvtool - OneRisk CSV utility (extract / extract-adj / merge in one script).
 
Actions:
  extract      Versions from .meta files (EOD/Region/Feed)       -> versions_output.csv
  extract-adj  Adjustment data from .meta + .json (Region/Feed)  -> adjustment_data_output.csv
  merge        Merge all CSV files from a folder into one         -> merged.csv
 
Examples:
  python csvtool.py extract     -i C:\\EOD      -o C:\\out
  python csvtool.py extract-adj -i C:\\EOD      -o C:\\out -n adj.csv
  python csvtool.py merge       -i C:\\csv_dir  -o C:\\out --no-validate
  python csvtool.py merge       -i C:\\csv_dir  -o C:\\out\\my_report.csv
 
-o can be a FOLDER (default file name is used) or a FILE path ending in .csv.
 
Exit codes: 0 = OK, 1 = error, 2 = nothing found (no .meta files).
"""
import argparse
import csv
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
 
log = logging.getLogger("csvtool")
NA = "N/A"
 
 
class ToolError(Exception):
    """Expected error: logged without traceback, exit code 1."""
 
 
# =============================================================================
# Shared helpers
# =============================================================================
 
VERSION_PATTERNS = [
    r'"version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"',
    r'version\s*:\s*([0-9]+\.[0-9]+\.[0-9]+)',
    r'version\s*=\s*["\']?([0-9]+\.[0-9]+\.[0-9]+)',
    r'\bv?([0-9]+\.[0-9]+\.[0-9]+)\b',
]
 
 
def read_version(meta_path: Path):
    """Version from a .meta file: JSON field 'version' first, then regex fallbacks."""
    try:
        content = meta_path.read_text(encoding="utf-8-sig")
    except OSError as e:
        log.warning("      Cannot read %s: %s", meta_path, e)
        return None
 
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "version" in data:
            return str(data["version"])
    except json.JSONDecodeError:
        pass
 
    for pattern in VERSION_PATTERNS:
        m = re.search(pattern, content, re.IGNORECASE)
        if m:
            return m.group(1)
    return None
 
 
def find_nested(data, key):
    """Case-insensitive recursive search for a key in nested dicts/lists."""
    key = key.lower()
    if isinstance(data, dict):
        for k, v in data.items():
            if k.lower() == key and v is not None:
                return v
            if isinstance(v, (dict, list)):
                found = find_nested(v, key)
                if found is not None:
                    return found
    elif isinstance(data, list):
        for item in data:
            found = find_nested(item, key)
            if found is not None:
                return found
    return None
 
 
def lookup(data, keys, check_body=True):
    """
    Find the first value for any of `keys` (in priority order):
      1) data['body'][key]   (if check_body)
      2) data[key]           (top level)
      3) recursive search
    """
    if not isinstance(data, (dict, list)):
        return None
 
    scopes = []
    if isinstance(data, dict):
        if check_body and isinstance(data.get("body"), dict):
            scopes.append(data["body"])
        scopes.append(data)
 
    for scope in scopes:
        for k in keys:
            if scope.get(k) is not None:
                log.debug("      Found %s directly", k)
                return scope[k]
 
    for k in keys:
        value = find_nested(data, k)
        if value is not None:
            log.debug("      Found %s in nested structure", k)
            return value
    return None
 
 
def load_first_json(folder: Path):
    """Load the first .json file (alphabetically) in the folder, or None."""
    json_files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".json")
    if not json_files:
        log.warning("      No JSON file found in: %s", folder)
        return None
 
    path = json_files[0]
    log.info("      Reading JSON: %s", path.name)
    try:
        with path.open(encoding="utf-8-sig") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("      Cannot parse %s: %s", path.name, e)
        return None
 
    log.debug("      JSON preview:\n%s...", json.dumps(data, indent=2)[:500])
    return data
 
 
def subdirs(folder: Path):
    try:
        return sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError as e:
        log.warning("Cannot read folder %s: %s", folder, e)
        return []
 
 
def iter_feeds_with_meta(root: Path):
    """
    Walk ROOT/<Region>/<Feed>/... and, for every feed, yield the FIRST directory
    (depth-first, alphabetical) that contains .meta files:
        (region_name, feed_name, dirpath, [meta file names])
    """
    if not root.is_dir():
        raise ToolError(f"Input folder does not exist: {root}")
 
    regions = subdirs(root)
    log.info("Found %d region folders: %s", len(regions), ", ".join(r.name for r in regions))
 
    for region in regions:
        feeds = subdirs(region)
        log.info("=" * 60)
        log.info("Region: %s (%d feed folders)", region.name, len(feeds))
 
        for feed in feeds:
            hit = None
            for dirpath, dirnames, filenames in os.walk(feed):
                dirnames.sort()
                metas = sorted(f for f in filenames if f.lower().endswith(".meta"))
                if metas:
                    hit = (Path(dirpath), metas)
                    break
 
            if hit is None:
                log.warning("  Feed: %s - no .meta file found", feed.name)
                continue
 
            log.info("  Feed: %s | Path: %s | .meta: %s", feed.name, hit[0], hit[1])
            yield region.name, feed.name, hit[0], hit[1]
 
 
def write_csv(path: Path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: NA if row.get(k) is None else row[k] for k in fields})
 
 
def log_summary(rows, columns):
    log.info("Summary by region:")
    for region in sorted({r["region"] for r in rows}):
        region_rows = [r for r in rows if r["region"] == region]
        log.info("  %s (%d feeds):", region, len(region_rows))
        for r in region_rows:
            details = " | ".join(f"{c}: {r.get(c) or NA}" for c in columns)
            log.info("    %s: %s", r["feed_name"], details)
 
 
def as_text(value):
    """Lists -> 'a,b,c'; everything else -> str; None stays None."""
    if value is None:
        return None
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return str(value)
 
 
# =============================================================================
# Action: extract  (versions)
# =============================================================================
 
META_KEYS = [
    "metaFileLocation", "meta_file_location", "MetaFileLocation",
    "metafilelocation", "meta_location", "metaLocation",
]
 
EXTRACT_FIELDS = ["feed_name", "dataset_key", "region", "version", "meta_file_location", "odf_path"]
 
 
def dataset_key(feed_name):
    """'IDS10232' from 'IDS10232-CREDIT-30049799'."""
    m = re.match(r"(IDS\d+)", feed_name)
    return m.group(1) if m else None
 
 
def odf_path(meta_location):
    """Everything before the last '/' of metaFileLocation."""
    if not meta_location:
        return None
    meta_location = str(meta_location)
    idx = meta_location.rfind("/")
    return meta_location[:idx] if idx != -1 else meta_location
 
 
def action_extract(args):
    rows = []
    for region, feed, dirpath, metas in iter_feeds_with_meta(args.input):
        data = load_first_json(dirpath)
        location = as_text(lookup(data, META_KEYS, check_body=False))
        log.info("      Meta File Location: %s", location or NA)
 
        for meta in metas:
            rows.append({
                "feed_name": feed,
                "dataset_key": dataset_key(feed),
                "region": region,
                "version": read_version(dirpath / meta),
                "meta_file_location": location,
                "odf_path": odf_path(location),
            })
    return finish_extract(args, rows, EXTRACT_FIELDS, ["dataset_key", "version"])
 
 
# =============================================================================
# Action: extract-adj  (adjustment data)
# =============================================================================
 
ADJ_META_KEYS = [
    "metaFileLocStdAdj",       # Standard adjustment (preferred)
    "metaFileLocExtAdj",       # Extended adjustment
    "metaFileLocProtoStdAdj",  # Proto standard
    "metaFileLocProtoExtAdj",  # Proto extended
] + META_KEYS
 
ADJ_FIELDS = ["feed_name", "region", "version", "meta_file_location",
              "adjustment_id", "adjustment_action", "impacted_ids"]
 
 
def action_extract_adj(args):
    rows = []
    for region, feed, dirpath, metas in iter_feeds_with_meta(args.input):
        data = load_first_json(dirpath)
        fields = {
            "meta_file_location": as_text(lookup(data, ADJ_META_KEYS)),
            "adjustment_id": as_text(lookup(data, ["adjustmentId"])),
            "adjustment_action": as_text(lookup(data, ["adjustmentAction"])),
            "impacted_ids": as_text(lookup(data, ["impactedIds"])),
        }
        for name, value in fields.items():
            log.info("      %s: %s", name, value or NA)
 
        if data is not None and fields["meta_file_location"] is None and isinstance(data, dict):
            log.warning("      Available top-level keys: %s", list(data.keys()))
            if isinstance(data.get("body"), dict):
                log.warning("      Available body keys: %s", list(data["body"].keys()))
 
        for meta in metas:
            rows.append({
                "feed_name": feed,
                "region": region,
                "version": read_version(dirpath / meta),
                **fields,
            })
    return finish_extract(args, rows, ADJ_FIELDS, ["version", "adjustment_action", "impacted_ids"])
 
 
def finish_extract(args, rows, fields, summary_columns):
    log.info("=" * 60)
    if not rows:
        log.warning("No .meta files found in any feed folder - CSV not written")
        return 2
 
    out_file = args.out_file
    write_csv(out_file, fields, rows)
    log.info("CSV saved to: %s", out_file)
    log.info("Total rows: %d", len(rows))
    log_summary(rows, summary_columns)
    return 0
 
 
# =============================================================================
# Action: merge
# =============================================================================
 
def action_merge(args):
    in_dir = args.input
    if not in_dir.is_dir():
        raise ToolError(f"Input folder does not exist: {in_dir}")
 
    out_file = args.out_file
    # Exclude the output file itself (matters when -i and -o are the same folder)
    files = [p for p in sorted(in_dir.glob("*.csv")) if p.resolve() != out_file.resolve()]
    if not files:
        raise ToolError(f"No CSV files found in: {in_dir}")
 
    # Read headers; skip empty files
    headers = {}
    for f in files:
        with f.open(newline="", encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh, delimiter=args.delimiter), None)
        if header is None:
            log.warning("Empty file skipped: %s", f.name)
            continue
        headers[f] = header
 
    if not headers:
        raise ToolError("All CSV files are empty")
 
    log.info("Found %d CSV files to merge:", len(headers))
    for f in headers:
        log.info("  - %s", f.name)
 
    ref_file, ref_header = next(iter(headers.items()))
 
    if args.validate:
        log.info("Validating column structure (reference: %s)...", ref_file.name)
        errors = []
        for f, header in headers.items():
            if header != ref_header:
                missing = [c for c in ref_header if c not in header]
                extra = [c for c in header if c not in ref_header]
                msg = f"Column mismatch in {f.name}"
                if missing:
                    msg += f" - Missing: {missing}"
                if extra:
                    msg += f" - Extra: {extra}"
                if not missing and not extra:
                    msg += " - Same columns, different order"
                errors.append(msg)
        if errors:
            for e in errors:
                log.error("  %s", e)
            raise ToolError("Column mismatch detected. All CSV files must have the same columns.")
        log.info("Column validation passed")
 
    total = 0
    with out_file.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, delimiter=args.delimiter)
        writer.writerow(ref_header)
        for f in headers:
            with f.open(newline="", encoding="utf-8-sig") as fh:
                reader = csv.reader(fh, delimiter=args.delimiter)
                next(reader)  # skip header
                for row in reader:  # streaming, no list() in memory
                    writer.writerow(row)
                    total += 1
 
    log.info("=" * 60)
    log.info("Merge completed: %s", out_file)
    log.info("Total rows merged: %s | Files processed: %d", f"{total:,}", len(headers))
    return 0
 
 
# =============================================================================
# CLI
# =============================================================================
 
ACTIONS = {
    #  name          function            help                                         default output name
    "extract":     (action_extract,     "Extract versions from .meta files",          "versions_output.csv"),
    "extract-adj": (action_extract_adj, "Extract adjustment data from .meta/.json",   "adjustment_data_output.csv"),
    "merge":       (action_merge,       "Merge all CSV files from a folder into one", "merged.csv"),
}
 
 
def build_parser():
    parser = argparse.ArgumentParser(
        prog="csvtool",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
 
    for name, (func, help_text, default_name) in ACTIONS.items():
        sp = sub.add_parser(name, help=help_text, description=help_text)
        sp.add_argument("-i", "--input", required=True, type=Path, help="Input folder")
        sp.add_argument("-o", "--output", required=True, type=Path,
                        help=f"Output folder (-> {default_name}) or file path ending in .csv")
        sp.add_argument("-n", "--name", default=default_name,
                        help=f"Output file name when -o is a folder (default: {default_name})")
        sp.add_argument("-v", "--verbose", action="store_true", help="Debug output (JSON previews etc.)")
        if name == "merge":
            sp.add_argument("--no-validate", dest="validate", action="store_false", help="Skip column validation")
            sp.add_argument("-d", "--delimiter", default=",", help="CSV delimiter (default: ',')")
        sp.set_defaults(func=func)
    return parser
 
 
def resolve_output(args):
    """-o C:\\out -> C:\\out\\<name>;  -o C:\\out\\report.csv -> exactly that file."""
    if args.output.suffix.lower() == ".csv":
        args.out_dir, args.out_file = args.output.parent, args.output
    else:
        args.out_dir, args.out_file = args.output, args.output / args.name
 
 
def setup_logging(out_dir: Path, action: str, verbose: bool):
    out_dir.mkdir(parents=True, exist_ok=True)
    log_file = out_dir / f"{action}_log_{datetime.now():%Y%m%d_%H%M%S}.log"
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in (logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    return log_file
 
 
def main(argv=None):
    # Windows CMD: avoid UnicodeEncodeError on non-ASCII paths/content
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
 
    args = build_parser().parse_args(argv)
    start = datetime.now()
 
    try:
        resolve_output(args)
        log_file = setup_logging(args.out_dir, args.action, args.verbose)
        log.info("Action: %s", args.action)
        log.info("Input:  %s", args.input.absolute())
        log.info("Output: %s", args.out_file.absolute())
        code = args.func(args)
    except ToolError as e:
        log.error("%s", e)
        return 1
    except Exception:
        log.exception("Unexpected error")
        return 1
 
    log.info("Processing time: %.2f s | Log file: %s",
             (datetime.now() - start).total_seconds(), log_file)
    return code
 
 
if __name__ == "__main__":
    sys.exit(main())
