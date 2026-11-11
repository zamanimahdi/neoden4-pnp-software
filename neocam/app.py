"""NeoDen4 camera + vision test app.

    python -m neocam.app [--host 192.168.1.50] [--port 5600] [--sim] [--config Config.ini]

Two panels, one per camera: live view, exposure / gain / window, save PNG, and the
NeoDen4 vision functions on the current frame (up camera: part on one nozzle or on all
four, against a background shot of the empty nozzles; head camera: fiducial search and
auto exposure). --sim starts the built-in camserver simulator.
"""
import argparse
import os
import sys
import threading
import time

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import vision as V
from .client import CamClient, CamError, UP, HEAD
from .config import NeoConfig


def to_qimage(img):
    img = np.ascontiguousarray(img)
    if img.ndim == 2:
        return QtGui.QImage(img.data, img.shape[1], img.shape[0], img.strides[0], QtGui.QImage.Format_Grayscale8).copy()
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return QtGui.QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QtGui.QImage.Format_RGB888).copy()


class ImageView(QtWidgets.QLabel):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 420)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setStyleSheet("background:#111; color:#888")
        self.setText("no image")
        self._img = None

    def show_image(self, img):
        self._img = to_qimage(img)
        self._rescale()

    def resizeEvent(self, e):
        self._rescale()
        super().resizeEvent(e)

    def _rescale(self):
        if self._img is not None:
            self.setPixmap(QtGui.QPixmap.fromImage(self._img).scaled(
                self.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))


class CamPanel(QtWidgets.QGroupBox):
    def __init__(self, app, cam, title):
        super().__init__(title)
        self.app, self.cam = app, cam
        self.frame = None            # oriented gray frame
        self.overlay = None
        lay = QtWidgets.QVBoxLayout(self)
        self.view = ImageView()
        lay.addWidget(self.view, 1)

        row = QtWidgets.QHBoxLayout()
        self.exp = QtWidgets.QSpinBox(minimum=1, maximum=65535, value=150 if cam == UP else 25)
        self.gain = QtWidgets.QSpinBox(minimum=0, maximum=255, value=8)
        self.win = QtWidgets.QComboBox()
        self.win.addItems(["1024", "800", "32"] if cam == HEAD else ["1024"])
        for lbl, w in (("Exposure", self.exp), ("Gain", self.gain), ("Window", self.win)):
            row.addWidget(QtWidgets.QLabel(lbl))
            row.addWidget(w)
        apply_b = QtWidgets.QPushButton("Apply")
        apply_b.clicked.connect(self.apply)
        row.addWidget(apply_b)
        lay.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        grab_b = QtWidgets.QPushButton("Grab")
        grab_b.clicked.connect(self.grab)
        self.live = QtWidgets.QCheckBox("Live")
        save_b = QtWidgets.QPushButton("Save PNG")
        save_b.clicked.connect(self.save)
        for w in (grab_b, self.live, save_b):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)

        self.tools = QtWidgets.QHBoxLayout()
        lay.addLayout(self.tools)
        self.result = QtWidgets.QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.result)

    # -- camera --
    @property
    def client(self):
        return self.app.client

    def apply(self):
        if not self.client:
            return
        try:
            n = int(self.win.currentText())
            self.client.window(self.cam, n, n)
            self.client.exposure(self.cam, self.exp.value())
            self.client.gain(self.cam, self.gain.value())
            self.app.status(f"{self.title()}: {n}x{n}, exposure {self.exp.value()}, gain {self.gain.value()}")
        except (CamError, OSError) as e:
            self.app.status(f"{self.title()}: {e}")

    def grab(self):
        if not self.client:
            return None
        try:
            raw = self.client.grab(self.cam)
        except (CamError, OSError) as e:
            self.app.status(f"{self.title()}: {e}")
            self.live.setChecked(False)
            return None
        self.frame = V.orient_up(raw) if self.cam == UP else V.orient_head(raw)
        self.overlay = None
        self.view.show_image(self.frame)
        return self.frame

    def show_overlay(self, img):
        self.overlay = img
        self.view.show_image(img)

    def save(self):
        if self.frame is None:
            return
        name = f"{'up' if self.cam == UP else 'head'}_{time.strftime('%Y%m%d_%H%M%S')}.png"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save frame", name, "PNG (*.png)")
        if path:
            cv2.imwrite(path, self.frame)
            if self.overlay is not None:
                cv2.imwrite(path[:-4] + "_result.png", self.overlay)


class UpPanel(CamPanel):
    def __init__(self, app):
        super().__init__(app, UP, "Up camera (parts on the nozzles)")
        self.backgrounds = {}
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["One nozzle (centre)", "Four nozzles"])
        self.pw = QtWidgets.QDoubleSpinBox(minimum=0, maximum=60, value=1.6, decimals=2, suffix=" mm")
        self.ph = QtWidgets.QDoubleSpinBox(minimum=0, maximum=60, value=0.8, decimals=2, suffix=" mm")
        bg_b = QtWidgets.QPushButton("Take background")
        bg_b.setToolTip("grab with empty nozzles; used for the current mode")
        bg_b.clicked.connect(self.take_background)
        an_b = QtWidgets.QPushButton("Analyze")
        an_b.clicked.connect(self.analyze)
        for w in (self.mode, QtWidgets.QLabel("Part"), self.pw, self.ph, bg_b, an_b):
            self.tools.addWidget(w)
        lo, hi = app.config.size_limits()
        self.lo = QtWidgets.QDoubleSpinBox(minimum=0.1, maximum=1.0, value=lo, decimals=2, singleStep=0.01)
        self.hi = QtWidgets.QDoubleSpinBox(minimum=1.0, maximum=5.0, value=hi, decimals=2, singleStep=0.01)
        self.stack_angle = QtWidgets.QSpinBox(minimum=-360, maximum=360, value=0, singleStep=90)
        self.nozzle = QtWidgets.QSpinBox(minimum=1, maximum=4, value=1)
        ver_b = QtWidgets.QPushButton("Correct + verify")
        ver_b.setToolTip("needs --machine: turn the nozzle by the measured angle, re-image, check (closed loop)")
        ver_b.clicked.connect(self.verify)
        row = QtWidgets.QHBoxLayout()
        for w in (QtWidgets.QLabel("Size ratio"), self.lo, self.hi, QtWidgets.QLabel("Stack angle"), self.stack_angle,
                  QtWidgets.QLabel("Nozzle"), self.nozzle, ver_b):
            row.addWidget(w)
        self.layout().insertLayout(self.layout().count() - 1, row)

    def verify(self):
        mb = self.app.machine
        bg = self.backgrounds.get(0)
        if mb is None:
            self.result.setText("Start the app with --machine tcp://<machine PC>:5602 (or sim://) to turn the nozzle.")
            return
        if bg is None:
            self.result.setText("Take a background first (one-nozzle mode, empty nozzle).")
            return
        p = V.roi_half_size(self.pw.value(), self.ph.value())
        n = self.nozzle.value()

        def measure():
            fr = self.grab()
            return V.analyze_nozzle(fr, bg, p) if fr is not None else V.PartResult(False)

        cfg = self.app.config
        try:
            r = V.correct_and_verify(measure, lambda d: mb.rotate(n, d, speed=150),
                                     float(cfg.vision("fVerifyAngleTol")), float(cfg.vision("fVerifyOffsetTol")),
                                     int(cfg.vision("nVerifyTries")))
        except Exception as e:                       # machine errors: not homed, link, ...
            self.result.setText(f"machine: {e}")
            return
        sc = V.size_check(r.as_part(), self.pw.value(), self.ph.value(), self.stack_angle.value(),
                          self.lo.value(), self.hi.value(), cfg.vision("sSizeOrientation"))
        self.result.setText(f"{'OK' if r.ok else 'REJECT'}: {r.reason}\n"
                            f"part angle at nozzle 0: {r.angle:+.2f}°, offset X {r.x:+.3f} Y {r.y:+.3f} mm, "
                            f"residuals {', '.join(f'{v:+.2f}' for v in (r.residuals or []))}\n"
                            f"size {r.w:.2f} x {r.h:.2f} mm, ratio {sc.rx:.2f} / {sc.ry:.2f}: {'ok' if sc.ok else sc.reason}")

    def take_background(self):
        if self.app.sim:
            self.client.led(UP, 0)                     # simulator: remove the parts
        img = self.grab()
        if self.app.sim:
            self.client.led(UP, 1)
        if img is not None:
            self.backgrounds[self.mode.currentIndex()] = img.copy()
            self.app.status("background stored for: " + self.mode.currentText())

    def analyze(self):
        bg = self.backgrounds.get(self.mode.currentIndex())
        if bg is None:
            self.result.setText("Take a background first (empty nozzles).")
            return
        fr = self.grab()
        if fr is None:
            return
        out = cv2.cvtColor(fr, cv2.COLOR_GRAY2BGR)
        lines = []
        if self.mode.currentIndex() == 0:
            res = [V.analyze_nozzle(fr, bg, V.roi_half_size(self.pw.value(), self.ph.value()))]
        else:
            res = V.analyze_nozzles(fr, bg, [True] * 4, self.app.config.sub_mouses)
        for i, r in enumerate(res):
            tag = "centre" if len(res) == 1 else f"nozzle {i + 1}"
            if not r.valid:
                lines.append(f"{tag}: no part")
                continue
            cx, cy, w, h, a = r.rect_px
            box = cv2.boxPoints(((cx, cy), (w, h), -a))
            cv2.polylines(out, [np.round(box).astype(np.int32)], True, (0, 255, 0), 2)
            cv2.drawMarker(out, (int(cx), int(cy)), (0, 0, 255), cv2.MARKER_CROSS, 30, 2)
            sc = V.size_check(r, self.pw.value(), self.ph.value(), self.stack_angle.value(), self.lo.value(),
                              self.hi.value(), self.app.config.vision("sSizeOrientation"))
            lines.append(f"{tag}: X {r.x:+.3f} mm  Y {r.y:+.3f} mm  angle {r.angle:+.2f}°  "
                         f"size {r.w:.2f} x {r.h:.2f} mm (expected {sc.exp_w:.2f} x {sc.exp_h:.2f}, ratio "
                         f"{sc.rx:.2f} / {sc.ry:.2f})  {'size ok' if sc.ok else 'SIZE REJECT'}")
        self.result.setText("\n".join(lines))
        self.show_overlay(out)


class HeadPanel(CamPanel):
    def __init__(self, app):
        super().__init__(app, HEAD, "Head camera (fiducials)")
        self.dmin = QtWidgets.QDoubleSpinBox(minimum=0.1, maximum=5, value=0.8, decimals=2, suffix=" mm")
        self.dmax = QtWidgets.QDoubleSpinBox(minimum=0.1, maximum=5, value=1.5, decimals=2, suffix=" mm")
        ae_b = QtWidgets.QPushButton("Auto exposure")
        ae_b.clicked.connect(self.auto_exposure)
        fm_b = QtWidgets.QPushButton("Find mark")
        fm_b.clicked.connect(self.find_mark)
        for w in (QtWidgets.QLabel("Mark Ø"), self.dmin, self.dmax, ae_b, fm_b):
            self.tools.addWidget(w)

    def auto_exposure(self):
        if not self.client:
            return
        self.win.setCurrentText("800")
        self.apply()

        def set_e(e):
            self.client.exposure(HEAD, e)

        e = V.auto_exposure_mark(lambda: self.grab(), set_e)
        self.exp.setValue(e)
        self.result.setText(f"auto exposure (fiducial): {e}, histogram mid level {V.histogram_mid_level(self.grab())}")

    def find_mark(self):
        fr = self.grab()
        if fr is None:
            return
        r = V.find_mark_best(fr, self.dmin.value(), self.dmax.value())
        out = cv2.cvtColor(fr, cv2.COLOR_GRAY2BGR)
        h, w = fr.shape
        cv2.drawMarker(out, (w // 2, h // 2), (255, 128, 0), cv2.MARKER_CROSS, 40, 1)
        if r.precision <= 0.7:
            self.result.setText("no fiducial found in this size range")
        else:
            cv2.circle(out, (int(round(r.x)), int(round(r.y))), int(r.r), (0, 255, 0), 2)
            cv2.drawMarker(out, (int(round(r.x)), int(round(r.y))), (0, 0, 255), cv2.MARKER_CROSS, 30, 2)
            dx, dy = V.mark_offset_mm(r, size=w)
            self.result.setText(f"mark at ({r.x:.2f}, {r.y:.2f}) px, Ø {2 * r.r / V.HEAD_PX_PER_MM:.3f} mm, "
                                f"precision {r.precision:.3f}, threshold {r.threshold}\n"
                                f"move head by X {dx:+.3f} mm, Y {dy:+.3f} mm to centre it")
        self.show_overlay(out)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, args):
        super().__init__()
        self.setWindowTitle("NeoDen4 cameras")
        self.client = None
        self.sim = args.sim
        self.machine = None
        if getattr(args, "machine", None):                 # neomach (PPP session): nozzle rotation for the closed loop
            try:
                from neomach import Mainboard
                self.machine = Mainboard.connect(args.machine)
            except Exception as e:                         # app still works as a camera viewer
                print(f"machine {args.machine}: {e}")
        self.config = NeoConfig.load(args.config) if args.config and os.path.exists(args.config) else NeoConfig()
        if not self.config.raw:                        # sample values from the machine's Config.ini
            self.config.raw = {f"Position/dPosSubMouses{i}.{a}": v for i, p in
                               enumerate([(161, 872), (888, 871), (159, 169), (887, 160)]) for a, v in zip("xy", p)}

        tb = self.addToolBar("connection")
        self.host = QtWidgets.QLineEdit(args.host)
        self.host.setFixedWidth(140)
        self.port = QtWidgets.QSpinBox(minimum=1, maximum=65535, value=args.port)
        conn = QtWidgets.QPushButton("Connect")
        conn.clicked.connect(self.connect_cam)
        cfg = QtWidgets.QPushButton("Load Config.ini")
        cfg.clicked.connect(self.load_config)
        for w in (QtWidgets.QLabel(" camserver "), self.host, self.port, conn, cfg):
            tb.addWidget(w)
        self.info = QtWidgets.QLabel("")
        tb.addWidget(self.info)

        split = QtWidgets.QSplitter()
        self.up, self.head = UpPanel(self), HeadPanel(self)
        split.addWidget(self.up)
        split.addWidget(self.head)
        self.setCentralWidget(split)
        self.statusBar()
        self.timer = QtCore.QTimer(self, interval=150, timeout=self.tick)
        self.timer.start()
        self.resize(1300, 820)

    def status(self, t):
        self.statusBar().showMessage(t, 8000)

    def connect_cam(self):
        try:
            if self.client:
                self.client.close()
            self.client = CamClient(self.host.text(), self.port.value())
            self.info.setText("  " + self.client.info())
            self.status("connected")
        except (OSError, CamError) as e:
            self.client = None
            self.info.setText("")
            self.status(f"cannot connect: {e}")

    def load_config(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "NeoDen4 Config.ini", "", "Config.ini (*.ini)")
        if path:
            self.config = NeoConfig.load(path)
            self.up.exp.setValue(self.config.up_exposure())
            self.status(f"config: fBrightness {self.config.brightness}, nozzles {self.config.sub_mouses}")

    def tick(self):
        for p in (self.up, self.head):
            if p.live.isChecked():
                p.grab()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5600)
    ap.add_argument("--sim", action="store_true", help="start the camserver simulator")
    ap.add_argument("--config", help="NeoDen4 Config.ini")
    ap.add_argument("--machine", help="mainboard for 'Correct + verify': tcp://<pc>:5602, serial://COM2 or sim://")
    ap.add_argument("--screenshot", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.sim:
        from . import simserver
        ev = threading.Event()
        threading.Thread(target=simserver.serve, kwargs=dict(port=args.port, host="127.0.0.1", ready=ev),
                         daemon=True).start()
        ev.wait(5)
        args.host = "127.0.0.1"
    app = QtWidgets.QApplication(sys.argv[:1])
    w = MainWindow(args)
    w.show()
    if args.sim:
        w.connect_cam()
    if args.screenshot:                     # self-test: run the vision buttons and save a picture
        def shot():
            w.up.take_background()
            w.up.analyze()
            w.head.win.setCurrentText("800")
            w.head.apply()
            w.head.find_mark()
            app.processEvents()
            w.grab().save(args.screenshot)
            print(w.up.result.text())
            print(w.head.result.text())
            app.quit()
        QtCore.QTimer.singleShot(300, shot)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
