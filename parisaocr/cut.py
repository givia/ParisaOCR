"""Cut the text lines of a scanned book for labelling (replaces `correct_lines.py cut`).

Pages are BOOK_DIR/p-NNN.png. All pages are detected (cached in BOOK_DIR/det);
pages with too few lines or in --exclude are dropped, the rest are shuffled
(--seed) and read one batch at a time until --test and --train pages are
accepted; a page whose mean word confidence is below --min-page-conf (tables,
maps, foreign text) is skipped. With --test-pages/--train-pages the given
pages are used as they are. Every detected line of an accepted page is
cropped from the original page at native resolution with a small margin into
LINES/SPLIT/p-NNN_LL.png (LL = line index in detection order) and described in
LINES/manifest.json with the draft reading and its low-confidence words, in the
format scripts/correct_lines.py serve, line_sheets.py and dataset.py read.
"""
import json
import pathlib
import random
import statistics
import sys

from PIL import Image

from .detect import detect_pages
from .pipeline import expand, line_crop, margins, recognize_batch


def parse_ranges(spec):
    pages = set()
    for part in filter(None, spec.split(",")):
        a, _, b = part.partition("-")
        pages.update(range(int(a), int(b or a) + 1))
    return pages


def cut(book_dir, out, detector, reader, opts, test=12, train=24, exclude="", seed=0,
        min_lines=15, min_page_conf=80.0, min_conf=60.0, pad=10, redo=False, tmp=None,
        test_pages="", train_pages=""):
    book_dir, out = pathlib.Path(book_dir), pathlib.Path(out)
    pages = [(p.stem, p) for p in sorted(book_dir.glob("p-*.png"))]
    if not pages:
        sys.exit(f"parisaocr cut: no p-NNN.png pages in {book_dir}")
    # Explicit pages (--test-pages/--train-pages) are taken as given, without the line-count
    # and confidence filters; otherwise pages are drawn at random until the quotas are met.
    explicit = {f"p-{p:03d}": split for split, spec in (("test", test_pages), ("train", train_pages)) for p in parse_ranges(spec)}
    missing = [p for p in explicit if p not in dict(pages)]
    if missing:
        sys.exit(f"parisaocr cut: no such pages in {book_dir}: {' '.join(missing)}")
    if explicit:
        pages = [(pid, path) for pid, path in pages if pid in explicit]
    records = {}
    for batch in detect_pages(detector, pages, book_dir / "det", redo):
        records.update({r["page"]: r for r in batch})
        print(f"detected {len(records)}/{len(pages)} pages", end="\r", flush=True)
    print()
    if explicit:
        order = [pid for pid, _ in pages]
        print(f"{len(order)} pages given explicitly")
    else:
        excluded = parse_ranges(exclude)
        order = [pid for pid, _ in pages if len(records[pid]["lines"]) >= min_lines and int(pid.split("-")[1]) not in excluded]
        random.Random(seed).shuffle(order)
        print(f"{len(order)} of {len(pages)} pages have at least {min_lines} lines and are not excluded")

    manifest, chosen, skipped = [], {}, []
    quota = {"test": test, "train": train}
    i = 0
    while i < len(order) and (explicit or sum(quota.values()) > 0):
        batch = order[i:i + detector.batch]
        i += len(batch)
        lines_of = recognize_batch([records[p] for p in batch], reader, opts, tmp)
        for pid in batch:
            lines = lines_of[pid]
            if explicit:
                split = explicit[pid]
            else:
                if sum(quota.values()) == 0:
                    break  # the rest of the batch was read for nothing; a batch is at most 8 pages
                words = [w for l in lines for w in l.words]
                mean = sum(w.conf for w in words) / len(words) if words else 0.0
                if mean < min_page_conf:
                    skipped.append((pid, round(mean)))
                    continue
                split = "test" if quota["test"] > 0 else "train"
                quota[split] -= 1
            chosen[pid] = split
            (out / split).mkdir(parents=True, exist_ok=True)
            with Image.open(records[pid]["image"]) as page:
                im = page.convert("L") if page.mode not in ("L", "1") else page.copy()
                # The saved crop gets the same margin rule as the recognizer's crop, in page pixels,
                # so training lines look like what the model sees at inference.
                heights = [l.bbox[3] - l.bbox[1] for l in lines]
                px, py = margins(pad, opts.pad_frac, statistics.median(heights) if heights else 0)
                boxes = [l.bbox for l in lines]
                rects = expand(boxes, px, py, im.width, im.height)
                if im.mode == "1":
                    im = im.convert("L")  # so neighbours can be painted over
                for k, (line, rect) in enumerate(zip(lines, rects)):  # detection order; a block box read as several lines gives several names
                    name = f"{pid}_{k:02d}"
                    line_crop(im, rect, boxes[k], boxes).save(out / split / f"{name}.png")
                    manifest.append({"name": name, "split": split, "page": pid, "draft": line.text,
                                     "check": [[w.text, None] for w in line.words if w.conf < min_conf],
                                     "bbox": list(line.bbox), "conf": line.conf})
    manifest.sort(key=lambda m: (m["split"] != "test", m["name"]))
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=0), encoding="utf-8")
    for split in ("test", "train"):
        ps = sorted(p for p, s in chosen.items() if s == split)
        print(f"{split}: {len(ps)} pages, {sum(m['split'] == split for m in manifest)} lines: " + " ".join(p[2:] for p in ps))
    if skipped:
        print(f"skipped {len(skipped)} pages below {min_page_conf:.0f} mean confidence: "
              + " ".join(f"{p[2:]}({c})" for p, c in skipped[:20]) + (" ..." if len(skipped) > 20 else ""))
    if not explicit and sum(quota.values()):
        print(f"short of {quota['test']} test and {quota['train']} train pages", file=sys.stderr)
    return manifest
