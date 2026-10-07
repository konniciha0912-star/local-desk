"""No network during extraction. Results always require review before sending."""
import json
from pathlib import Path
import sys
import zipfile
import xml.etree.ElementTree as ET

LIMIT = 20000

def image_text(image):
    from PIL import Image
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import OCRVersion,LangRec
    import numpy as np
    if not isinstance(image,Image.Image): image=Image.open(image)
    if image.width*image.height>20_000_000: raise ValueError("画像は2,000万画素以下にしてください。")
    model=Path(__file__).resolve().parent / ".runtime/ocr/japan_rec_crnn_v2.onnx"
    detector=model.parent / "ch_PP-OCRv4_det_infer.onnx"
    if not model.is_file() or not detector.is_file(): raise ValueError("日本語OCRモデル未導入です。setup_ocr.pyを実行してください。")
    engine=RapidOCR(params={"Global.use_cls":False,"Global.log_level":"error","Det.model_path":str(detector),"Det.ocr_version":OCRVersion.PPOCRV4,"Rec.model_path":str(model),"Rec.rec_img_shape":[3,32,320],"Rec.lang_type":LangRec.JAPAN,"Rec.ocr_version":OCRVersion.PPOCRV4,"EngineConfig.onnxruntime.intra_op_num_threads":4,"EngineConfig.onnxruntime.inter_op_num_threads":1})
    result=engine(np.asarray(image.convert("RGB")))
    return "\n".join(result.txts or []), any(float(score)<0.8 for score in (result.scores or []))

def document(path):
    pieces=[]; warnings=[]
    if path.suffix.lower()==".pdf":
        from pypdf import PdfReader
        reader=PdfReader(path)
        if reader.is_encrypted: raise ValueError("暗号化されたPDFは解除したコピーを選んでください。")
        for index,page in enumerate(reader.pages[:100]):
            text=page.extract_text() or ""
            if not text.strip():
                for image in list(page.images)[:3]:
                    from io import BytesIO
                    value,low=image_text(BytesIO(image.data));text += "\n"+value
                warnings.append(f"PDF {index+1}ページはOCRを使用しました。誤読を確認してください。")
            pieces.append(f"[PDF {path.name} p.{index+1}]\n{text}")
            if sum(map(len,pieces))>=LIMIT: break
        if len(reader.pages)>100: warnings.append("100ページまで読み取りました。")
    elif path.suffix.lower()==".docx":
        with zipfile.ZipFile(path) as archive:
            entry=archive.getinfo("word/document.xml")
            if entry.file_size>10*1024*1024: raise ValueError("Word本文が大きすぎます。")
            root=ET.fromstring(archive.read(entry))
        namespace={"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        for paragraph in root.findall(".//w:p",namespace):
            pieces.append("".join(value.text or "" for value in paragraph.findall(".//w:t",namespace)))
            if sum(map(len,pieces))>=LIMIT: break
        warnings.append("本文・表の文字を読み取りました。図・ヘッダー・変更履歴は参照していません。")
    else:
        text,low=image_text(path);pieces=[text];warnings.append("画像OCRの結果です。数字・固有名詞を確認してください。")
        if low: warnings.append("認識の確信度が低い箇所があります。")
    text="\n".join(pieces)
    if len(text)>LIMIT: warnings.append("20,000文字までの読み取りです。後半を省略しました。")
    if not text.strip(): warnings.append("文字を取り出せませんでした。画像が鮮明か確認してください。")
    return {"text":text[:LIMIT],"name":path.name,"warnings":warnings,"local":True}

def audio(path,model):
    if not (model / "model.bin").is_file(): raise ValueError("音声モデル未導入です。setup_voice.pyを実行してください。")
    import av
    with av.open(str(path)) as container:
        if container.duration and container.duration/av.time_base > 300: raise ValueError("音声は5分以下にしてください。")
    from faster_whisper import WhisperModel
    engine=WhisperModel(str(model),device="cpu",compute_type="int8",cpu_threads=4,local_files_only=True)
    segments,info=engine.transcribe(str(path),beam_size=3,vad_filter=True)
    pieces=[]
    for segment in segments:
        if segment.end>300: break
        pieces.append(segment.text)
        if sum(map(len,pieces))>LIMIT: break
    return {"text":"\n".join(pieces)[:LIMIT],"name":path.name,"language":info.language,"warnings":["ローカル音声認識の結果です。誤認識を確認してから送信してください。"],"local":True}

if __name__=="__main__":
    try:
        path=Path(sys.argv[2]);result=document(path) if sys.argv[1]=="document" else audio(path,Path(sys.argv[3]))
    except ImportError:
        result={"error":"文字・音声の読み取り依存が未導入です。Install-Desktop.ps1を実行してください。"}
    except Exception as error:
        result={"error":str(error)[:500] if isinstance(error,ValueError) else "資料を読み取れませんでした。破損・対応形式を確認してください。"}
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode("utf-8"));sys.stdout.flush()
