# Third-party notices

## Hermes Agent (Nous Research), MIT

The pilot runs the Hermes Agent container image `nousresearch/hermes-agent`
tag `rc.4-v0.21.6-amd64`, pinned by digest
`sha256:ffa4fbb471c2bc8c85fbcfc629347585cc1586c86d2e6db465d01203ce696d95`
(provenance revision `818c13be1dc4fd28987e1e881a9408224afd4535`), and uses its
bundled `skills/productivity/powerpoint` skill **from that image at runtime**.
No Hermes source file is copied into this repository. `images/skill-blobs.txt`
lists only the git blob hashes used to verify the skill files at build time.

One sentence is reproduced verbatim in `agent/typed_driver.py` so that both arms
use the same image-description prompt: "Fully describe and explain everything
about this image, then answer the following question:" (from Hermes
`tools/vision_tools.py`). It is used under the Hermes MIT license:

```
MIT License

Copyright (c) 2026 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The three parity helpers in `agent/helpers/` are original code of this
repository (MIT). They are copied into the skill's `scripts/` directory inside
the built image so both arms can use them.

## Other components

python-pptx, lxml, Pillow, XlsxWriter, numpy (pip, pinned versions), LibreOffice,
poppler-utils and the Noto / Noto CJK / IPA fonts (Debian packages, versions
recorded per build in `apt-pins.txt`) are installed at build time on the CI
runner under their own licenses; none are vendored here.
