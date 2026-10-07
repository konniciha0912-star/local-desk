# Third-party components

Local DeskのソースはMITライセンスです。外部のモデル・ライブラリ・実行環境にはそれぞれのライセンスが適用されます。ソースZIPにモデル重み、Python本体、CUDA実行環境、ダウンロードした依存パッケージは含めません。

同梱した表示ライブラリ：

- [Marked 15.0.12](https://github.com/markedjs/marked) — MIT。全文は `web/vendor/marked-LICENSE.txt`。
- [Prism 1.30.0](https://github.com/PrismJS/prism) — MIT。全文は `web/vendor/prism-LICENSE.txt`。

初回セットアップで取得するもの：

- [Python](https://docs.python.org/3/license.html) — PSF。専用Pythonを使う場合はMicrosoftのNuGetにある公式Python 3.13.16を使用。
- [pywebview](https://github.com/r0x0r/pywebview) — BSD-3-Clause。Microsoft WebView2はMicrosoftの配布条件に従います。
- [PrismMLのllama.cpp](https://github.com/PrismML-Eng/llama.cpp/blob/adfffbe41/LICENSE) — MIT。固定版 `prism-b10743-adfffbe`。CUDA版の各実行ライブラリには各提供元の条件が適用されます。
- [Bonsai 2 27B](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf) — Apache-2.0。
- [Qwen3.5 9B](https://huggingface.co/Qwen/Qwen3.5-9B) — Apache-2.0。量子化版は各配布元も確認してください。
- [Ornith 1.5 9B](https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF) — MIT。
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[Whisper base](https://huggingface.co/Systran/faster-whisper-base) — MIT。
- [RapidOCR 3.9.2](https://github.com/RapidAI/RapidOCR/blob/v3.9.2/LICENSE) — Apache-2.0。[OCRモデル](https://huggingface.co/spaces/RapidAI/RapidOCR)は同配布元の固定リビジョンとSHA-256を `setup_ocr.py` に記録しています。
- [pypdf](https://github.com/py-pdf/pypdf) — BSD-3-Clause。
- [PyAV](https://github.com/PyAV-Org/PyAV) — BSD-3-Clause。FFmpeg等の条件はその配布物に従います。
- [sounddevice](https://github.com/spatialaudio/python-sounddevice) — MIT。PortAudioの条件も適用されます。

依存の固定版は `requirements-desktop.lock.txt` に記録しています。各パッケージのライセンス情報は導入後の `.runtime/desktop/Lib/site-packages/*dist-info/` で確認できます。独自モデルを登録するときは、そのモデルの配布元・利用条件を登録してください。
