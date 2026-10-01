"""Alternatives to Surya behind the same detector interface (`prepare`, `detect`, `batch`).

`PaddleDetector`: the PP-OCR text detector (PaddleOCR weights, Apache-2.0) run with
ONNX Runtime through RapidOCR. The default, PP-OCRv6 small, ships with the package
(models/ppocrv6-det-small.onnx, RapidOCR's ONNX conversion); other sizes are fetched by RapidOCR. It returns one quadrilateral per text line; parisaocr
keeps its bounding rectangle.

`KrakenSegDetector`: Kraken's default baseline segmenter (blla, ships with the
Apache-2.0 package). It returns a baseline and a boundary polygon per line; parisaocr
keeps the polygon's bounding rectangle.

`make_detector(spec, ...)` builds one from a command-line spec:
    surya                     Surya (the default)
    ppocr[:VERSION-SIZE[:UNCLIP]]  e.g. ppocr, ppocr:v6-medium, ppocr:v6-small:1.3, ppocr:v5-server
                              (UNCLIP: how far DB boxes are grown around the text, default 1.6)
    kraken                    Kraken's default segmenter
"""
import pathlib

import numpy as np

from .detect import LineDetector

BUNDLED_DETECTOR = pathlib.Path(__file__).resolve().parent / "models" / "ppocrv6-det-small.onnx"


class PaddleDetector(LineDetector):
    def __init__(self, min_width=1600, max_width=2000, batch=8, cpu=False, version="v6", size="medium",
                 unclip_ratio=1.6, box_thresh=0.5):
        super().__init__(min_width, max_width, batch, cpu)
        self.version, self.size = version, size
        self.unclip_ratio, self.box_thresh = unclip_ratio, box_thresh

    def model(self):
        if self._model is None:
            from rapidocr import LangDet, ModelType, OCRVersion
            from rapidocr.ch_ppocr_det import TextDetector
            from rapidocr.main import DEFAULT_CFG_PATH, root_dir
            from rapidocr.utils.log import logger as rapidocr_logger
            from rapidocr.utils.parse_parameters import ParseParams
            rapidocr_logger.setLevel("ERROR")  # no INFO lines about engines and model paths
            params = {
                # PP-OCRv6 has one detector for all scripts; RapidOCR only checks the label against
                # its recognizers' language list (which has no Persian), so any listed one will do.
                "Det.lang_type": LangDet.EN if self.version == "v6" else LangDet.CH,
                "Det.ocr_version": OCRVersion.PPOCRV6 if self.version == "v6" else OCRVersion.PPOCRV5,
                "Det.model_type": ModelType(self.size),
                # ParisaOCR has already scaled the page (prepare); the detector must not shrink it again
                "Det.limit_type": "max", "Det.limit_side_len": 4000,
                "Det.unclip_ratio": self.unclip_ratio, "Det.box_thresh": self.box_thresh,
            }
            if (self.version, self.size) == ("v6", "small") and BUNDLED_DETECTOR.exists():
                params["Det.model_path"] = str(BUNDLED_DETECTOR)  # shipped with the package: no download
            cfg = ParseParams.update_batch(ParseParams.load(DEFAULT_CFG_PATH), params)
            if cfg.Global.model_root_dir is None:
                cfg.Global.model_root_dir = root_dir / "models"
            # Only the detector: RapidOCR's own pipeline would also fetch recognition models we never use.
            cfg.Det.engine_cfg = cfg.EngineConfig[cfg.Det.engine_type.value]
            cfg.Det.model_root_dir = cfg.Global.model_root_dir
            self._model = TextDetector(cfg.Det)
        return self._model

    def detect(self, images):
        out = []
        for im in images:
            res = self.model()(np.ascontiguousarray(np.asarray(im)[:, :, ::-1]))  # RGB -> BGR
            lines = []
            if res.boxes is not None:
                for box, score in zip(res.boxes, res.scores):
                    xs, ys = box[:, 0], box[:, 1]
                    lines.append({"bbox": [round(float(xs.min())), round(float(ys.min())), round(float(xs.max())), round(float(ys.max()))],
                                  "polygon": [[round(float(x)), round(float(y))] for x, y in box],
                                  "confidence": round(float(score), 3)})
            out.append(lines)
        return out


class KrakenSegDetector(LineDetector):
    def model(self):
        if self._model is None:
            from kraken.configs import SegmentationInferenceConfig
            from kraken.ketos.util import to_ptl_device
            from kraken.tasks import SegmentationTaskModel
            import torch
            accelerator, device = to_ptl_device("cpu" if self.cpu or not torch.cuda.is_available() else "cuda:0")
            self._config = SegmentationInferenceConfig(accelerator=accelerator, device=device, text_direction="horizontal-rl")
            self._model = SegmentationTaskModel.load_model()
        return self._model

    def detect(self, images):
        out = []
        for im in images:
            seg = self.model().predict(im, self._config)
            lines = []
            for line in seg.lines:
                pts = line.boundary or []
                if len(pts) < 3:
                    continue
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                lines.append({"bbox": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))],
                              "polygon": [[round(x), round(y)] for x, y in pts], "confidence": 1.0})
            out.append(lines)
        return out


def make_detector(spec, min_width, max_width, batch, cpu):
    name, _, variant = (spec or "surya").partition(":")
    if name == "surya":
        return LineDetector(min_width, max_width, batch, cpu)
    if name == "ppocr":
        model, _, unclip = (variant or "v6-medium").partition(":")
        version, _, size = model.partition("-")
        return PaddleDetector(min_width, max_width, batch, cpu, version=version, size=size or "medium",
                              unclip_ratio=float(unclip) if unclip else 1.6)
    if name == "kraken":
        return KrakenSegDetector(min_width, max_width, batch, cpu)
    raise SystemExit(f"parisaocr: unknown detector {spec!r} (surya, ppocr[:v6-medium|v6-small|v5-server|...], kraken)")
