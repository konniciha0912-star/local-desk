"""Pinned Japanese OCR recognizer. Detection remains in the locked OCR package."""
import json
from pathlib import Path
from setup_bonsai import download,digest

REVISION="3702ce8b03d6f64083c275cda2b0ce9440e68fd3"
MODEL="japan_rec_crnn_v2.onnx"
SHA256="2b0495059f5738166e606d864b04ff00093f67a807efb02cddf472839cae970c"
ROOT=Path(__file__).resolve().parent / ".runtime/ocr"
DETECTOR="ch_PP-OCRv4_det_infer.onnx"
DETECTOR_SHA="d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9"

def main():
    path=download(f"https://huggingface.co/spaces/RapidAI/RapidOCR/resolve/{REVISION}/models/text_rec/{MODEL}",ROOT/MODEL,3571807,SHA256)
    detector=download(f"https://huggingface.co/spaces/RapidAI/RapidOCR/resolve/{REVISION}/models/text_det/{DETECTOR}",ROOT/DETECTOR,4745517,DETECTOR_SHA)
    (ROOT/"manifest.json").write_text(json.dumps({"revision":REVISION,"files":[{"name":item.name,"sha256":digest(item)} for item in [path,detector]],"source":"https://huggingface.co/spaces/RapidAI/RapidOCR"},indent=2),encoding="utf-8")
    print("Japanese OCR recognizer installed and verified.")
if __name__=="__main__":main()
