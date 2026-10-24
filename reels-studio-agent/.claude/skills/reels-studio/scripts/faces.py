"""Поиск лица в кадре: для кадрирования, зумов и выбора обложки.

Основной детектор — YuNet (нейросеть OpenCV, models/, MIT): видит мелкие лица
в зале и лица вполоборота. Если его нет — Haar из OpenCV 4.x.
"""
import sys
from pathlib import Path

YUNET = Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx"
_det = {}


def _cv():
    try:
        import cv2
        return cv2
    except ImportError:
        return None


def _detector():
    if "kind" in _det:
        return _det["kind"]
    cv2 = _cv()
    _det["kind"] = None
    if cv2 is not None and YUNET.exists() and hasattr(cv2, "FaceDetectorYN"):
        _det["yunet"] = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.6, 0.3, 50)
        _det["kind"] = "yunet"
    elif cv2 is not None and hasattr(cv2, "CascadeClassifier"):
        _det["haar"] = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        _det["kind"] = "haar"
    return _det["kind"]


def available():
    return _detector() is not None


def detect(frame):
    """Главное лицо в кадре: {cx, cy, size, score, sharp} в долях кадра, или None."""
    cv2 = _cv()
    kind = _detector()
    if kind is None:
        return None
    scale = 1280 / max(frame.shape[:2])
    small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    if kind == "yunet":
        _det["yunet"].setInputSize((sw, sh))
        _, faces = _det["yunet"].detect(small)
        if faces is None:
            return None
        # главное лицо — уверенное и покрупнее (зрители на заднем плане мельче)
        f = max(faces, key=lambda r: float(r[14]) * (0.5 + float(r[3]) / sh * 10))
        x, y, w, h, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])
    else:
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        faces = _det["haar"].detectMultiScale(gray, 1.1, 5, minSize=(24, 24))
        if not len(faces):
            return None
        x, y, w, h = map(float, max(faces, key=lambda r: r[2] * r[3]))
        score = 0.7
    x0, y0 = max(int(x), 0), max(int(y), 0)
    roi = cv2.cvtColor(small[y0:y0 + int(h), x0:x0 + int(w)], cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(roi, cv2.CV_64F).var()) if roi.size else 0.0
    return {"cx": (x + w / 2) / sw, "cy": (y + h / 2) / sh, "size": h / sh,
            "score": score, "sharp": sharp}


def frames_at(video, times):
    """Кадры (BGR) в заданные моменты исходника."""
    cv2 = _cv()
    cap = cv2.VideoCapture(str(video))
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        yield t, (frame if ok else None)
    cap.release()


def segment_faces(video, edl):
    """Медианный центр лица (cx, cy) по каждому куску; None — лица нет."""
    if not available():
        print("OpenCV не найден — кадрирую без поиска лица", file=sys.stderr)
        return [None] * len(edl)
    res = []
    for s in edl:
        times = [s["start"] + f * (s["end"] - s["start"]) for f in (0.2, 0.5, 0.8)]
        found = [d for _, fr in frames_at(video, times) if fr is not None
                 for d in [detect(fr)] if d]
        if found:
            xs = sorted(d["cx"] for d in found)
            ys = sorted(d["cy"] for d in found)
            res.append((xs[len(xs) // 2], ys[len(ys) // 2]))
        else:
            res.append(None)
    return res
