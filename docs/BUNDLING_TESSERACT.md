# Putting Tesseract inside the project

Tesseract is what lets the platform read scanned documents. Without it, scans
are flagged `ocr_unavailable` and raised for someone to type in by hand —
correct behaviour, but slow.

Normally you install it into the operating system. That needs administrator
rights and, usually, internet access. Where the BMS host allows neither, you can
put a copy **inside the project folder** instead. The platform finds it on its
own, and it travels with the folder when you copy it to the server.

---

## The short version

On a machine that *does* have internet and admin rights:

1. Install Tesseract normally, **including the Arabic language data**.
2. Run the bundling script.
3. Copy the whole project folder to the BMS host.

```powershell
powershell -ExecutionPolicy Bypass -File deploy\bundle-tesseract.ps1   # Windows
```

```bash
./deploy/bundle-tesseract.sh                                          # Linux
```

Nothing needs to be installed on the BMS host, and no administrator rights are
required there.

## How the platform finds it

In this order:

1. **`BMS_TESSERACT_CMD`**, if you set it. An explicit setting always wins.
2. **`vendor/tesseract/`** inside the project — the bundled copy.
3. **`tesseract` on the system PATH** — a normal installation.

So bundling requires no configuration at all. If you had previously set
`BMS_TESSERACT_CMD` and now want the bundled copy used, unset it.

## Getting Tesseract in the first place

**Windows** — the UB Mannheim build:
<https://github.com/UB-Mannheim/tesseract/wiki>

During setup, expand **Additional language data** and tick **Arabic**. This is
easy to miss and there is no way to add it later without re-running the
installer. BMS documents need it.

**Debian / Ubuntu**:

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-ara
```

## What the scripts do

**Windows** is a straight folder copy. `tesseract.exe`, its DLLs and `tessdata/`
all move to `vendor\tesseract\`, and Windows loads DLLs from beside the
executable, so it just works. Expect 100–200 MB depending on how many languages
you included.

**Linux** needs more care, because a system `tesseract` links against shared
libraries that will not exist on the target host. The script therefore also
copies every library it depends on into `vendor/tesseract/lib/` and replaces the
binary with a small wrapper that points the loader at them. About 56 libraries
in a typical case.

Both scripts finish by running the bundled copy's `--list-langs`, so you see
immediately whether it works and which languages it has — before you copy
anything to the server.

## Language data

Tesseract cannot find its language files unless it is told where they are. A
system install knows its own location; a bundled one does not, and fails with an
unhelpful `Error opening data file`.

The platform handles this: when the Tesseract it is using has a `tessdata/`
folder beside it, it sets `TESSDATA_PREFIX` automatically. A system install is
left alone, because overriding it would break a working setup.

Which languages are used comes from `BMS_OCR_LANGUAGES`, default `eng+ara`. If
you bundle a copy without Arabic, set it to `eng` — otherwise every OCR call
fails rather than falling back.

## Checking it worked

```bash
curl -s http://127.0.0.1:8000/health
```

```json
{ "ocr_engines": ["plain_text", "tesseract"], ... }
```

`tesseract` in that list means the platform found it and it runs. If it is
absent, the copy is missing, not executable, or `BMS_OCR_ENABLED` is `false`.

To see exactly what was resolved:

```bash
cd app && python3 -c "
from bms.config import Settings, bundled_tesseract
s = Settings()
print('bundled :', bundled_tesseract())
print('using   :', s.tesseract_cmd)
print('tessdata:', s.tessdata_dir)
"
```

## Keeping it out of git

`vendor/` is gitignored. It is 100–200 MB of third-party binaries, it is
platform-specific, and it is rebuilt by running the script — none of which
belongs in version control. It travels by copying the folder, not by cloning.

## Licensing

Tesseract is Apache 2.0, which permits redistribution. `vendor/tesseract/`
keeps the upstream `LICENSE` file that ships with it; leave it in place.

## If you would rather not bundle

Installing Tesseract into the operating system is still the simpler option
wherever it is allowed, and the platform prefers whatever is on PATH when
nothing is bundled. Bundling exists for hosts where that is not possible — it is
not the recommended default.

And the platform works without Tesseract at all. Scans are marked
`ocr_unavailable` and raised for manual entry; nothing is ever guessed. OCR
makes the work faster, it is not what makes it correct.
