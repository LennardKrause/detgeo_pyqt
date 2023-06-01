import os, sys, json
import numpy as np
import pyqtgraph as pg
from PyQt6 import QtWidgets, QtCore, QtGui
from pyFAI import calibrant
import Dans_Diffraction as dif

class MainWindow(pg.QtWidgets.QMainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # set path home
        self.path = os.path.dirname(__file__)
        # add an icon
        self.setWindowIcon(QtGui.QIcon(os.path.join(self.path, 'icon.png')))

        # Drag-and-Drop cif-file
        #  dropEvent()
        #  - check dropped file is a cif
        #  get_cif_reference()
        #  - use gemmi to get cell, centring and crystal  system from cif
        #  - use pyFAI get_d_spacings() to create contours
        self.setAcceptDrops(True)

        # menubar is displayed within the main window on Windows
        # so we need to make space for it
        # no idea about other OS, if there are issues fix them here
        if sys.platform == 'win32':
            self.offset_win32 = self.menuBar().height()
        else:
            self.offset_win32 = 0
        
        file_dump = os.path.join(self.path, 'settings.json')
        # save parameters to file
        # - save_default: overwrite existing file with defaults
        # - force_write: overwrite existing file after load
        self.init_par(file_dump, save_default=False, force_write=True)

        # What standards should be available as reference
        # The d spacings will be imported from pyFAI
        self.geo.ref_library = calibrant.names()
        # dict to store custom reference data
        self.geo.ref_custom = {}
        self.geo.ref_custom_hkl = {}

        # define grid layout
        self.layout = pg.QtWidgets.QGridLayout()
        
        # make a widget, set the layout
        centralwidget = pg.QtWidgets.QWidget()
        centralwidget.setLayout(self.layout)

        self.setCentralWidget(centralwidget)
        
        # get the detector specs
        self.detectors = self.get_det_library()

        # pick current detector
        self.det = self.get_specs_det(self.detectors, self.geo.det_type, self.geo.det_size)
        
        # add the plot to the layout
        self.ax = pg.plot(useCache=True, pxMode=True)
        # added to avoid the error:
        # qt.pointer.dispatch: skipping QEventPoint(id=0 ts=0 pos=0,0 scn=482.023,246.011
        # gbl=482.023,246.011 Released ellipse=(1x1 ∡ 0) vel=0,0 press=-482.023,-246.011
        # last=-482.023,-246.011 Δ 482.023,246.011) : no target window
        self.ax.viewport().setAttribute(QtCore.Qt.WidgetAttribute.WA_AcceptTouchEvents, False)
        self.layout.addWidget(self.ax)

        # translate unit for plot title
        self.geo.unit_names = ['2\U0001D6F3 [\u00B0]', 'd [\u212B\u207B\u00B9]', 'q [\u212B]', 'sin(\U0001D6F3)/\U0001D706 [\u212B]']
        if self.geo.unit >= len(self.geo.unit_names):
            print(f'Error: Valid geo.unit range is from 0 to {len(self.geo.unit_names)-1}, geo.unit={self.geo.unit}')
            raise SystemExit

        # init the hkl tooltip
        font = QtGui.QFont()
        font.setPixelSize(self.plo.cont_ref_hkl_size)
        font.setBold(True)
        QtWidgets.QToolTip.setFont(font)

        # initialize the detector screen
        self.init_screen()
        
        # populate the menus with detectors, references and units
        self.init_menus()
        self.sliderWidget = SliderWidget(self, self.geo, self.plo, self.lmt)
        self.setStyleSheet('''
                SliderWidget {
                    border: 1px outset darkGray;
                    border-radius: 4px;
                    background: #aad3d3d3;
                }
                SliderWidget:hover {
                    background: #aad3d3d3;
                }
            ''')

    def init_screen(self):
        # init the plot for contours and beam center
        self.ax.setAspectLocked()
        #remove axes
        self.ax.getPlotItem().hideAxis('bottom')
        self.ax.getPlotItem().hideAxis('left')
        # disable pan/zoom
        self.ax.setMouseEnabled(x=False, y=False)
        # disable right click  context menu
        self.ax.setMenuEnabled(False)
        # hide the autoscale button
        self.ax.hideButtons()

        # get colormap
        self.plo.cont_cmap = pg.colormap.get(self.plo.cont_geom_cmap_name)

        # add beam center scatter plot
        self.plo.beam_center = pg.ScatterPlotItem()
        self.ax.addItem(self.plo.beam_center)

        # figure out proper plot dimensions
        self.plo.xdim = (self.det.hms * self.det.hmn + self.det.pxs * self.det.hgp * self.det.hmn + self.det.cbh)/2
        self.plo.ydim = (self.det.vms * self.det.vmn + self.det.pxs * self.det.vgp * self.det.vmn + self.det.cbh)/2
        
        # limit the axis x and y
        self.ax.setXRange(-self.plo.xdim, self.plo.xdim, padding=0)
        self.ax.setYRange(-self.plo.ydim, self.plo.ydim, padding=0)
        
        # resize the window
        self.resize(int(self.plo.plot_size*self.plo.xdim/self.plo.ydim), self.plo.plot_size + self.offset_win32)

        # scale contour grid to detector size
        self.plo.cont_grid_max = int(np.ceil(max(self.plo.xdim, self.plo.ydim)))
        
        # generate contour levels
        self.plo.cont_geom_num = np.linspace(self.plo.cont_tth_min, self.plo.cont_tth_max, self.plo.cont_tth_num)

        # container for contour lines
        self.plo.contours = {'conic':[], 'ref':[], 'labels':[]}
        # add empty plot per contour line
        font = QtGui.QFont()
        font.setPixelSize(self.plo.cont_geom_label_size)
        font.setBold(True)
        for i in range(self.plo.cont_tth_num):
            self.plo.contours['conic'].append(self.ax.plot(useCache=True, antialias=True))
            temp_label = pg.TextItem(anchor=(0.5,0.5), fill=pg.mkBrush('w'))
            temp_label.setFont(font)
            self.plo.contours['labels'].append(temp_label)
            self.ax.addItem(temp_label)


        # add empty plot per reference contour line
        for i in range(self.plo.cont_ref_num):
            self.plo.contours['ref'].append(self.ax.plot(useCache=True, antialias=True))
            self.plo.contours['ref'][i].setAlpha(self.plo.cont_ref_alpha, False)
            self.plo.contours['ref'][i].setCurveClickable(True, width=self.plo.cont_ref_lw)
            self.plo.contours['ref'][i].sigClicked.connect(self.show_tooltip)
            self.plo.contours['ref'][i].name = None
        
        # build detector modules
        self.build_detector()

        # add unit label
        self.add_unit_label()

        # create cones and draw contour lines
        self.update_screen()

    def init_menus(self):
        menuBar = QtWidgets.QMenuBar()
        self.setMenuBar(menuBar)

        menu_det = menuBar.addMenu('Detector')
        group_det = QtGui.QActionGroup(self)
        group_det.setExclusive(True)

        # menu Detectors
        for d in self.detectors:
            d = d.upper()
            d_menu = QtWidgets.QMenu(d, self)
            d_menu.setStatusTip('')
            menu_det.addMenu(d_menu)
            for s in self.detectors[d]['size']:
                s = s.upper()
                det_action = QtGui.QAction(s, self, checkable=True)
                self.set_menu_action(det_action, self.change_detector, d, s)
                d_menu.addAction(det_action)
                group_det.addAction(det_action)
                if d == self.geo.det_type.upper() and s == self.geo.det_size.upper():
                    det_action.setChecked(True)
        
        self.menu_ref = menuBar.addMenu('Reference')
        self.group_ref = QtGui.QActionGroup(self)
        self.group_ref.setExclusive(True)
        
        # menu Reference: add None
        ref_action = QtGui.QAction('None', self, checkable=True)
        self.set_menu_action(ref_action, self.change_reference, 'None')
        self.menu_ref.addAction(ref_action)
        self.group_ref.addAction(ref_action)
        if 'None' == self.geo.reference:
            ref_action.setChecked(True)
        
        # menu Reference: add pyFAI library
        self.sub_menu_pyFAI = QtWidgets.QMenu('pyFAI', self)
        self.sub_menu_pyFAI.setStatusTip('')
        self.menu_ref.addMenu(self.sub_menu_pyFAI)
        for ref_name in self.geo.ref_library:
            ref_action = QtGui.QAction(ref_name, self, checkable=True)
            self.set_menu_action(ref_action, self.change_reference, ref_name)
            self.sub_menu_pyFAI.addAction(ref_action)
            self.group_ref.addAction(ref_action)
            if ref_name == self.geo.reference:
                ref_action.setChecked(True)

        # menu Reference: add Custom
        self.sub_menu_custom = QtWidgets.QMenu('Custom', self)
        self.sub_menu_custom.setStatusTip('Drag and Drop *.cif files.')
        self.menu_ref.addMenu(self.sub_menu_custom)
        
        # menu Units
        menu_unit = menuBar.addMenu('Units')
        group_unit = QtGui.QActionGroup(self)
        group_unit.setExclusive(True)
        for unit_index, unit_name in enumerate(self.geo.unit_names):
            unit_action = QtGui.QAction(unit_name, self, checkable=True)
            self.set_menu_action(unit_action, self.change_units, unit_index)
            menu_unit.addAction(unit_action)
            group_unit.addAction(unit_action)
            if unit_index == self.geo.unit:
                unit_action.setChecked(True)

    def add_unit_label(self):
        font = QtGui.QFont()
        font.setPixelSize(self.plo.unit_label_size)
        self.unit_label = pg.TextItem(anchor=(0.0,0.0), color=self.plo.unit_label_color, fill=self.plo.unit_label_fill)
        self.unit_label.setText(self.geo.unit_names[self.geo.unit])
        self.unit_label.setFont(font)
        self.ax.addItem(self.unit_label)
        self.unit_label.setPos(-self.plo.xdim, self.plo.ydim)

    def set_menu_action(self, action, target, *args):
        action.triggered.connect(lambda: target(*args))

    def set_window_title(self):
        if self.geo.reference == 'None':
            self.setWindowTitle(self.det.name)
        else:
            self.setWindowTitle(f'{self.det.name} - {self.geo.reference}')

    def change_detector(self, det_name, det_size):
        self.det = self.get_specs_det(self.detectors, det_name, det_size)
        self.ax.clear()
        self.init_screen()
        self.sliderWidget.center_frame()

    def change_units(self, unit_index):
        self.geo.unit = unit_index
        self.unit_label.setText(self.geo.unit_names[unit_index])
        self.draw_contours()

    def change_reference(self, ref_name):
        self.geo.reference = ref_name
        self.get_reference()
        self.draw_reference()

    def get_cif_reference(self, fpath):
        xtl = dif.Crystal(fpath)
        # :return xval: arrray : x-axis of powder scan (units)
        # :return inten: array : intensity values at each point in x-axis
        # :return reflections: (h, k, l, xval, intensity) array of reflection positions, grouped by min_overlap
        xval, inten, reflections = xtl.Scatter.powder(scattering_type='xray', units='dspace', powder_average=True, min_overlap=0.02)
        # reject low intensities: based on median or mean?
        # median is always around unity -> useless
        # mean rejects many, add adjustable multiplicator?
        used = reflections[reflections[:,4] > np.mean(reflections[:,4])]
        # sort by intensity -> ascending -> flip
        ordered = used[used[:, 4].argsort()][::-1]
        # pick the strongest
        ordered = ordered[:self.plo.cont_ref_num]
        # assign dspacing
        self.plo.cont_ref_dsp = ordered[:,3]
        # hkl -> integer
        # cast hkl array to list of tuples (for easy display)
        hkl = ordered[:,:3].astype(int)
        #self.plo.cont_ref_hkl = list(zip(hkl[:,0], hkl[:,1], hkl[:,2], ordered[:,4]))
        self.plo.cont_ref_hkl = list(zip(hkl[:,0], hkl[:,1], hkl[:,2]))

        self.geo.reference = os.path.basename(fpath)
        self.geo.ref_custom[self.geo.reference] = self.plo.cont_ref_dsp
        self.geo.ref_custom_hkl[self.geo.reference] = self.plo.cont_ref_hkl

        ref_action = QtGui.QAction(self.geo.reference, self, checkable=True)
        self.set_menu_action(ref_action, self.change_reference, self.geo.reference)
        self.sub_menu_custom.addAction(ref_action)
        self.group_ref.addAction(ref_action)
        ref_action.setChecked(True)
        
        # update window title
        self.set_window_title()

        self.draw_reference()

    def get_reference(self):
        if self.geo.reference in self.geo.ref_library:
            # get the d spacings for the calibrtant from pyFAI
            self.plo.cont_ref_dsp = np.array(calibrant.get_calibrant(self.geo.reference).get_dSpacing()[:self.plo.cont_ref_num])
            self.plo.cont_ref_hkl = None
        elif self.geo.reference in self.geo.ref_custom:
            # get custom d spacings
            self.plo.cont_ref_dsp = self.geo.ref_custom[self.geo.reference]
            self.plo.cont_ref_hkl = self.geo.ref_custom_hkl[self.geo.reference]
        else:
            # set all d-spacings to -1
            self.plo.cont_ref_dsp = np.zeros(self.plo.cont_ref_num) -1
            self.plo.cont_ref_hkl = None
        # update window title
        self.set_window_title()

    def get_specs_geo(self):
        ######################
        # Setup the geometry #
        ######################
        geo = container()
        geo.det_type = 'EIGER2' # [str]  Pilatus3 / Eiger2
        geo.det_size = '4M'     # [str]  300K 1M 2M 6M / 1M 4M 9M 16M
        geo.ener = 21.0         # [keV]  Beam energy
        geo.dist = 75.0         # [mm]   Detector distance
        geo.yoff = 0.0          # [mm]   Detector offset (vertical)
        geo.xoff = 0.0          # [mm]   Detector offset (horizontal)
        geo.rota = 25.0         # [deg]  Detector rotation
        geo.tilt = 0.0          # [deg]  Detector tilt
        geo.unit = 1            # [0-3]  Contour legend
                                #          0: 2-Theta
                                #          1: d-spacing
                                #          2: q-space
                                #          3: sin(theta)/lambda
        geo.reference = 'None'  # [str]  Plot reference contours
                                #          pick from pyFAI
        return geo

    def get_specs_plo(self):
        ################
        # Plot Details #
        ################
        plo = container()
        # - geometry contour section - 
        plo.cont_tth_min = 5                # [int]    Minimum 2-theta contour line
        plo.cont_tth_max = 150              # [int]    Maximum 2-theta contour line
        plo.cont_tth_num = 30               # [int]    Number of contour lines
        plo.cont_geom_cmark = 'o'           # [marker] Beam center marker (geometry)
        plo.cont_geom_csize = 6             # [int]    Beam center size (geometry)
        plo.cont_geom_lw = 4.0              # [float]  Contour linewidth
        plo.cont_geom_label_size = 14       # [int]    Contour label size
        plo.cont_geom_cmap_name = 'viridis' # [cmap]   Contour colormap (geometry)
        # - reference contour section - 
        plo.cont_ref_alpha = 0.25           # [float]  Reference contour alpha
        plo.cont_ref_color = 'gray'         # [color]  Reference contour color
        plo.cont_ref_lw = 5.0               # [float]  Reference contour linewidth
        plo.cont_ref_num = 60               # [int]    Number of reference contours
        plo.cont_ref_hkl_size = 14          # [int]    Font size of hkl tooltipß
        # - module section - 
        plo.module_alpha = 0.20             # [float]  Detector module alpha
        plo.module_color = 'gray'           # [color]  Detector module color
        # - general section - 
        plo.cont_steps = 500                # [int]    Contour steps
        plo.plot_size = 768                 # [int]    Plot size, px
        plo.unit_label_size = 16            # [int]    Label size, px
        plo.unit_label_color = 'gray'       # [str]    Label color
        plo.unit_label_fill = 'white'       # [str]    Label fill color
        # -slider section - 
        plo.action_ener = True              # [bool]   Show energy slider
        plo.action_dist = True              # [bool]   Show distance slider
        plo.action_rota = True              # [bool]   Show rotation slider
        plo.action_yoff = True              # [bool]   Show vertical offset slider
        plo.action_xoff = True              # [bool]   Show horizontal offset slider
        plo.action_tilt = True              # [bool]   Show tilt slider

        return plo

    def get_specs_lmt(self):
        ##########
        # Limits #
        ##########
        lmt = container()
        lmt.ener_min =  5.0    # [float] Energy minimum [keV]
        lmt.ener_max =  100.0  # [float] Energy maximum [keV]
        lmt.ener_stp =  1.0    # [float] Energy step size [keV]
        lmt.dist_min =  40.0   # [float] Distance minimum [mm]
        lmt.dist_max =  1000.0 # [float] Distance maximum [mm]
        lmt.dist_stp =  1.0    # [float] Distance step size [mm]
        lmt.xoff_min = -150.0  # [float] Horizontal offset minimum [mm]
        lmt.xoff_max =  150.0  # [float] Horizontal offset maximum [mm]
        lmt.xoff_stp =  1.0    # [float] Horizontal offset step size [mm]
        lmt.yoff_min =  0.0    # [float] Vertical offset minimum [mm]
        lmt.yoff_max =  250.0  # [float] Vertical offset maximum [mm]
        lmt.yoff_stp =  1.0    # [float] Vertical offset step size [mm]
        lmt.rota_min = -60.0   # [float] Rotation minimum [deg]
        lmt.rota_max =  60.0   # [float] Rotation maximum [deg]
        lmt.rota_stp =  1.0    # [float] Rotation step size [deg]
        lmt.tilt_min = -30.0   # [float] Tilt minimum [deg]
        lmt.tilt_max =  30.0   # [float] Tilt maximum [deg]
        lmt.tilt_stp =  1.0    # [float] Tilt step size [deg]
        
        return lmt

    def get_specs_det(self, detectors, det_type, det_size):
        det_type = det_type.upper()
        det_size = det_size.upper()

        if det_type not in detectors.keys():
            print('Unknown detector type!')
            raise SystemExit
        
        if det_size not in detectors[det_type]['size'].keys():
            print('Unknown detector type/size combination!')
            raise SystemExit
        
        det = container()
        det.hms = detectors[det_type]['hms']
        det.vms = detectors[det_type]['vms']
        det.pxs = detectors[det_type]['pxs']
        det.hgp = detectors[det_type]['hgp']
        det.vgp = detectors[det_type]['vgp']
        det.cbh = detectors[det_type]['cbh']
        det.hmn, det.vmn = detectors[det_type]['size'][det_size]
        det.name = f'{det_type} {det_size}'

        return det

    def get_det_library(self):
        ###########################
        # Detector Specifications #
        ###########################
        detectors = dict()
            ###############################
            # Specifications for Pilatus3 #
            ###############################
        detectors['PILATUS3'] = {
            'hms' : 83.8,    # [mm]  Module size (horizontal)
            'vms' : 33.5,    # [mm]  Module size (vertical)
            'pxs' : 172e-3,  # [mm]  Pixel size
            'hgp' : 7,       # [pix] Gap between modules (horizontal)
            'vgp' : 17,      # [pix] Gap between modules (vertical)
            'cbh' : 0,       # [mm]  Central beam hole
            'size' : {'300K':(1,3),'1M':(2,5),'2M':(3,8),'6M':(5,12)},
            }
            ###############################
            # Specifications for Pilatus4 #
            ###############################
        detectors['PILATUS4'] = {
            'hms' : 75.0,    # [mm]  Module size (horizontal)
            'vms' : 39.0,    # [mm]  Module size (vertical)
            'pxs' : 150e-3,  # [mm]  Pixel size
            'hgp' : 8,       # [pix] Gap between modules (horizontal)
            'vgp' : 12,      # [pix] Gap between modules (vertical)
            'cbh' : 0,       # [mm]  Central beam hole
            'size' : {'260K':(1,2),'800K':(2,3),'1M':(2,4),'1.5M':(3,4),'2M':(3,6),'3M':(4,6)}
            }
        
            #############################
            # Specifications for Eiger2 #
            #############################
        detectors['EIGER2'] = {
            'hms' : 77.1,    # [mm]  Module size (horizontal)
            'vms' : 38.4,    # [mm]  Module size (vertical)
            'pxs' : 75e-3,   # [mm]  Pixel size
            'hgp' : 38,      # [pix] Gap between modules (horizontal)
            'vgp' : 12,      # [pix] Gap between modules (vertical)
            'cbh' : 0,       # [mm]  Central beam hole
            'size' : {'1M':(1,2),'4M':(2,4),'9M':(3,6),'16M':(4,8)},
            }
        
            #############################
            # Specifications for MPCCD #
            #############################
        detectors['MPCCD'] = {
            'hms' : 51.2,    # [mm]  Module size (horizontal)
            'vms' : 25.6,    # [mm]  Module size (vertical)
            'pxs' : 50e-3,   # [mm]  Pixel size
            'hgp' : 18,      # [pix] Gap between modules (horizontal)
            'vgp' : 27,      # [pix] Gap between modules (vertical)
            'cbh' : 3,       # [mm]  Central beam hole
            'size' : {'4M':(2,4)},
            }
        
        # make file dump
        file_dump = os.path.join(self.path, 'detectors.json')
        if not os.path.exists(file_dump):
            with open(file_dump, 'w') as wf:
                json.dump(detectors, wf, indent=4)
        else:
            with open(file_dump, 'r') as of:
                detectors = json.load(of)
        
        return detectors

    def build_detector(self):
        # build detector modules
        # beam position is between the modules (even) or at the center module (odd)
        # determined by the "+det.hmn%2" part
        for i in range(-self.det.hmn//2+self.det.hmn%2, self.det.hmn-self.det.hmn//2):
            for j in range(-self.det.vmn//2+self.det.vmn%2, self.det.vmn-self.det.vmn//2):
                # - place modules along x (i) and y (j) keeping the gaps in mind ( + (det.hgp*det.pxs)/2)
                # - the " - ((det.hms+det.hgp*det.pxs)/2)" positions the origin (the beam) at the center of a module
                #   and "det.hmn%2" makes sure this is only active for detectors with an odd number of modules
                # - define sets of panels that collectively move to realize a central hole offset for MPCCD detectors
                #   that are used at SACLA/SPring-8:
                #   x = (...) + (det.cbh/2)*(2*(j&det.vmn)//det.vmn-1)
                #   y = (...) + (det.cbh/2)*(1-2*(i&det.hmn)//det.hmn)
                # - negative values of det.cbh for 'clockwise' offset order
                origin_x = i * (self.det.hms + self.det.hgp * self.det.pxs) \
                             - ((self.det.hms + self.det.hgp * self.det.pxs)/2) * (self.det.hmn % 2) \
                             + (self.det.hgp * self.det.pxs)/2 \
                             + (self.det.cbh/2) * (2*(j & self.det.vmn) // self.det.vmn-1)
                origin_y = j * (self.det.vms + self.det.vgp * self.det.pxs) \
                             - ((self.det.vms + self.det.vgp * self.det.pxs)/2) * (self.det.vmn%2) \
                             + (self.det.vgp * self.det.pxs)/2 \
                             + (self.det.cbh/2) * (1-2*(i & self.det.hmn) // self.det.hmn)
                # add the module
                rect_item = pg.QtWidgets.QGraphicsRectItem(origin_x, origin_y,  self.det.hms, self.det.vms)
                rect_item.setPen(pg.mkPen(color = self.plo.module_color, width = 0))
                rect_item.setBrush(pg.mkBrush(color = self.plo.module_color))
                rect_item.setOpacity(self.plo.module_alpha)
                self.ax.addItem(rect_item)

    def draw_contours(self):
        # calculate the offset of the contours resulting from yoff and rotation
        # shift the grid to draw the cones, to make sure the contours are drawn
        # within the visible area
        _comp_shift = -(self.geo.yoff + np.tan(np.deg2rad(self.geo.rota))*self.geo.dist)
        # update beam center
        self.plo.beam_center.setData([self.geo.xoff],[_comp_shift],
                                     symbol = self.plo.cont_geom_cmark,
                                     size = self.plo.cont_geom_csize,
                                     brush = pg.mkBrush(self.plo.cont_cmap.map(0, mode='qcolor')))
        # compensation to revert the rotated distance for the tilt
        for _n, _ttd in enumerate(self.plo.cont_geom_num):
            # current fraction for colormap
            _f = _n/len(self.plo.cont_geom_num)

            # convert theta in degrees to radians
            theta = np.deg2rad(_ttd)

            # convert theta in degrees to radians
            # for some reason I defined it negative some time ago
            # now there's no turning back!
            omega = -np.deg2rad(self.geo.tilt + self.geo.rota)

            # calculate the conic section corresponding to the theta angle
            # :returns False is conic is outside of visiblee area
            conic, label_pos = self.calc_conic(omega, theta, steps=self.plo.cont_steps)
            if conic is False:
                self.plo.contours['conic'][_n].setVisible(False)
                self.plo.contours['labels'][_n].setVisible(False)
                continue

            # plot the conic section
            self.plo.contours['conic'][_n].setData(conic, pen=pg.mkPen(self.plo.cont_cmap.map(_f, mode='qcolor'), width=self.plo.cont_geom_lw))
            self.plo.contours['conic'][_n].setVisible(True)

            # Conversion factor keV to Angstrom: 12.398
            # sin(t)/l: np.sin(Theta) / lambda -> (12.398/geo_energy)
            stl = np.sin(theta/2)/(12.398/self.geo.ener)

            # d-spacing: l = 2 d sin(t) -> 1/2(sin(t)/l)
            dsp = 1/(2*stl)

            # prepare the values in the different units / labels
            #_units = {0:np.rad2deg(theta), 1:_dsp, 2:_stl*4*np.pi, 3:_stl}
            _units = {0:_ttd, 1:dsp, 2:stl*4*np.pi, 3:stl}
            self.plo.contours['labels'][_n].setPos(self.geo.xoff, label_pos)
            self.plo.contours['labels'][_n].setText(f'{_units[self.geo.unit]:.2f}', color=self.plo.cont_cmap.map(_f, mode='qcolor'))
            self.plo.contours['labels'][_n].setVisible(True)

    def draw_reference(self):
        # plot reference contour lines
        # standard contour lines are to be drawn
        for _n in range(self.plo.cont_ref_num):
            # number of d-spacings might be lower than the maximum number of allowed contours
            if _n < len(self.plo.cont_ref_dsp):
                _d = self.plo.cont_ref_dsp[_n]
                # lambda = 2 * d * sin(theta)
                # 2-theta = 2 * (lambda / 2*d)
                # lambda -> (12.398/geo_energy)
                lambda_d = (12.398/self.geo.ener) / (2*_d)
                if lambda_d > 1.0:
                    continue
                
                # get theta
                theta = 2 * np.arcsin(lambda_d)
                
                # convert theta in degrees to radians
                # for some reason I defined it negative some time ago
                # now there's no turning back!
                omega = -np.deg2rad(self.geo.tilt + self.geo.rota)
                
                # skip relative theta > +-90 deg contours
                if theta < np.pi/2 + abs(omega):
                    self.plo.contours['ref'][_n].setVisible(True)
                else:
                    self.plo.contours['ref'][_n].setVisible(False)
                    continue
                
                # calculate the conic section corresponding to the theta angle
                # :returns False is conic is outside of visiblee area
                conic = self.calc_conic(omega, theta, steps=self.plo.cont_steps)
                if not conic:
                    self.plo.contours['ref'][_n].setVisible(False)
                    continue

                # plot the conic section
                self.plo.contours['ref'][_n].setData(conic, pen=pg.mkPen(self.plo.cont_ref_color, width=self.plo.cont_ref_lw))
                
                # if hkl are available
                # put them in the proper container for the contour
                # so indexing gets it right
                if self.plo.cont_ref_hkl:
                    self.plo.contours['ref'][_n].name = self.plo.cont_ref_hkl[_n]
                else:
                    self.plo.contours['ref'][_n].name = None
            else:
                # there are more available contours than we have from the cif/pyFAI
                # hide the superfluous ones
                self.plo.contours['ref'][_n].setVisible(False)
    
    def calc_conic(self, omega, theta, steps=500):
        # This functions documentation is in a terrible state!
        # I really hope I can find the time to comment the steps
        # not only to make sure I don't forget what I was thinking
        # but to make your life a little easier understanding it.


        # skip drawing smaller/larger +-90 deg contours
        # reject overlap of the 'backscattering'
        # -> limitation of the geometric model
        if theta > np.pi/2 + abs(omega):
            return False, False

        dy_cone = self.geo.dist * np.tan(omega)
        dz_cone = np.sqrt(self.geo.dist**2 + dy_cone**2)
        comp_tilt = np.deg2rad(self.geo.tilt) * self.geo.dist

        ecc = np.round(np.cos(np.pi/2 - omega) / np.cos(theta), 10)
        e21 = ecc**2-1

        y1 = dz_cone * np.sin(theta) / np.cos(omega + theta)
        y2 = dz_cone * np.sin(theta) / np.cos(omega - theta)

        # add x/y offsets
        # revert tilt rotation
        y0 = dy_cone - self.geo.yoff + comp_tilt 
        x0 = self.geo.xoff

        if 0 <= abs(ecc) < 1:
            # circle/ellipse
            t = np.linspace(-np.pi, np.pi, steps)
            yd = (y1-y2)/2
            h = (y1+y2)/2
            w = dz_cone * np.sin(theta) * (y1+y2) / (2 * np.sqrt(y1*y2) * np.cos(theta))
            x = x0 + w * np.sin(t)
            y = y0 + yd + h * np.cos(t)
        elif abs(ecc) == 1:
            # parabola
            t = np.linspace(-10, 10, steps)
            yd = np.sign(ecc) * self.geo.dist * np.tan(abs(omega) - theta)
            a = np.sign(ecc) * self.geo.dist * np.tan(theta)
            x = x0 + a*t
            y = y0 - dy_cone + yd + a/2*t**2
        elif 1 < abs(ecc) < 100:
            # hyperbola
            t = np.linspace(-np.pi, np.pi, steps)
            yd = (y1-y2)/2
            h = np.sign(omega) * (y1+y2)/2
            w = h * np.sqrt(e21)
            x = x0 + w * np.sinh(t)
            y = y0 + yd - h * np.cosh(t)
        elif abs(ecc) >= 100:
            # line
            t = np.linspace(-10, 10, steps)
            a = -np.sign(ecc) * min(abs(y1), abs(y2)) / 2
            x = x0 + a*t
            y = y0 + np.ones(len(t))*2*a
        
        # check value overlap with detector area
        # add a margin to make sure contours are
        # spanning the full area -> coarse stepsize
        max_dim_x = self.plo.xdim * 1.1
        max_dim_y = self.plo.ydim * 1.1
        cx = np.argwhere((x >= -max_dim_x) & (x <= max_dim_x))
        cy = np.argwhere((y >= -max_dim_y) & (y <= max_dim_y))
        # crop and reject
        if len(cy) == 0 or len(cx) == 0:
            # outside detector area
            return False, False
        else:
            # crop the contour
            # this does not crop ellipses reaching far out
            # to cut those I need to figure out how to come
            # up with proper 'connect' values as the plotting
            # algorithm would connect the loose ends across the screen!
            # this is to cut the 2d array:
            #c = np.vstack([x, y]).T
            #contour = c[(c[:,0] >= -max_dim_y) & (c[:,0] <= max_dim_y) & (c[:,1] >= -max_dim_x) & (c[:,1] <= max_dim_x)]
            #
            #print(np.rad2deg(theta), x[cx.min()], x[cx.max()], y[cy.min()], y[cy.max()], self.plo.xdim, self.plo.ydim)
            l = list(range(max(cx.min(), cy.min()), min(cx.max(), cy.max())+1))
            x = x[l]
            y = y[l]
        
        # position labels according to rotation angle
        if omega <= 0:
            label_pos = max(y) if theta < np.pi/2 else min(y)
        else:
            label_pos = min(y) if theta < np.pi/2 else max(y)
        return np.vstack([x, y]).T, label_pos
        
    def calc_cone(self, X, Y, Z, rotmat, comp, xoff, yoff):
        # rotate the sample around y
        t = np.transpose(np.array([X,Y,Z]), (1,2,0))
        # apply rotation
        X,Y,Z = np.transpose(np.dot(t, rotmat), (2,0,1))
        return Y+xoff, X+comp-yoff, Z

    def show_tooltip(self, widget, event):
        if not widget.name or not self.plo.cont_ref_hkl:
            return False
        pos = QtCore.QPoint(*map(int, event.screenPos()))# - QtCore.QPoint(10,20)
        QtWidgets.QToolTip.showText(pos, str(widget.name))
        event.ignore()

    def update_screen(self, val=None):
        if val is not None:
            if self.sender().objectName() == 'dist':
                self.geo.dist = float(val)
            elif self.sender().objectName() == 'rota':
                self.geo.rota = float(val)
            elif self.sender().objectName() == 'tilt':
                self.geo.tilt = float(val)
            elif self.sender().objectName() == 'yoff':
                self.geo.yoff = float(val)
            elif self.sender().objectName() == 'xoff':
                self.geo.xoff = float(val)
            elif self.sender().objectName() == 'ener':
                self.geo.ener = float(val)
        # re-calculate cones and re-draw contours
        self.draw_contours()
        # draw reference contours
        if self.geo.reference != 'None':
            self.get_reference()
            self.draw_reference()

    def dragEnterEvent(self, event):
        # Drag-and-Drop cif-file
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        # Drag-and-Drop cif-file
        #  dropEvent()
        #  - check dropped file is a cif
        fpath = event.mimeData().urls()[0].toLocalFile()
        if os.path.splitext(fpath)[1] == '.cif':
            self.get_cif_reference(fpath)

    def init_par(self, file_dump, save_default, force_write):
        # fetch the geometry, detector, plot specifications and limits
        # load the defaults
        # geo: geometry and detector specs
        self.geo = self.get_specs_geo()
        # plo: plot details
        self.plo = self.get_specs_plo()
        # lmt: geometry limits
        self.lmt = self.get_specs_lmt()
        # file name to store current settings
        # if file_dump doesn't exists, make a dump
        if not os.path.exists(file_dump) or save_default:
            self.save_par(file_dump)
        # if it exists load parameters
        else:
            self.load_par(file_dump)
        
        if force_write:
            self.save_par(file_dump)

    def save_par(self, save_as):
        # Writing geo as dict to file
        with open(save_as, 'w') as wf:
            json.dump({'geo':self.geo.__dict__, 'plo':self.plo.__dict__, 'lmt':self.lmt.__dict__}, wf, indent=4)

    def load_par(self, save_as):
        # Opening JSON file as dict
        with open(save_as, 'r') as of:
            pars = json.load(of)
        conv = {'geo':self.geo, 'plo':self.plo, 'lmt':self.lmt}
        for key, vals in pars.items():
                for p, x in vals.items():
                    setattr(conv[key], p, x)

class container(object):
    pass

class SliderWidget(QtWidgets.QFrame):
    def __init__(self, parent, geo, plo, lmt):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.geo = geo
        self.plo = plo
        self.lmt = lmt
        self.leaveEvent = self.toggle_panel
        self.enterEvent = self.toggle_panel
        frame = QtWidgets.QFrame()
        frame.setFixedHeight(12)
        self.box_width_add = 60
        layout.addWidget(frame)

        frame.setStyleSheet('''
            QFrame {
                border: 1px solid darkGray;
                border-radius: 2px;
                background: #aa646464;
            }
        ''')

        self.box = QtWidgets.QGroupBox()
        layout.addWidget(self.box)
        self.box.setHidden(True)
        self.box_toggle = False
        self.box_width_dynamic = 0
        self.box_height_show = int(parent.size().height()/3)
        self.box_height_hide = int(frame.size().height())

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setRowStretch(1,10)
        self.box.setLayout(grid)
        
        _idx = 0
        if plo.action_ener:
            self.add_slider(grid, 'Energy\n[keV]', 'ener', _idx, self.geo.ener, lmt.ener_min, lmt.ener_max, lmt.ener_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1
        if plo.action_dist:
            self.add_slider(grid, 'Distance\n[mm]', 'dist', _idx, self.geo.dist, lmt.dist_min, lmt.dist_max, lmt.dist_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1
        if plo.action_yoff:
            self.add_slider(grid, 'Y offset\n[mm]', 'yoff', _idx, self.geo.yoff, lmt.yoff_min, lmt.yoff_max, lmt.yoff_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1
        if plo.action_xoff:
            self.add_slider(grid, 'X offset\n[mm]', 'xoff', _idx, self.geo.xoff, lmt.xoff_min, lmt.xoff_max, lmt.xoff_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1
        if plo.action_tilt:
            self.add_slider(grid, 'Tilt\n[˚]', 'tilt', _idx, self.geo.tilt, lmt.tilt_min, lmt.tilt_max, lmt.tilt_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1
        if plo.action_rota:
            self.add_slider(grid, 'Rotation\n[˚]', 'rota', _idx, self.geo.rota, lmt.rota_min, lmt.rota_max, lmt.rota_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1
        
        self.resize(self.box_width_dynamic, self.box_height_hide)
        self.center_frame()

    def center_frame(self):
        self.move(int((self.parent().size().width()-self.box_width_dynamic)/2), self.parent().offset_win32)

    def update_slider(self, label, value):
        label.setText(str(int(value)))

    def add_slider(self, layout, label, token, idx, lval, lmin, lmax, lstp):
        label_name = QtWidgets.QLabel(label)
        label_name.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        
        label_value = QtWidgets.QLabel()
        label_value.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Vertical, objectName=token)
        slider.setValue(999)
        slider.valueChanged.connect(self.parent().update_screen)
        slider.valueChanged.connect(lambda value: self.update_slider(label_value, value))
        slider.setRange(int(lmin), int(lmax))
        slider.setSingleStep(int(lstp))
        slider.setPageStep(int(lstp))
        slider.setValue(int(lval))
        
        layout.addWidget(label_name, 0, idx, QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(slider, 1, idx, QtCore.Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(label_value, 2, idx, QtCore.Qt.AlignmentFlag.AlignCenter)

        return slider

    def toggle_panel(self, event):
        if type(event) == QtGui.QEnterEvent:
            #self.box.setHidden(not self.box.isHidden())
            self.box.setHidden(False)
            self.resize(self.box_width_dynamic, self.box_height_show)
        elif type(event) == QtCore.QEvent and not self.box_toggle:
            self.box.setHidden(True)
            self.resize(self.box_width_dynamic, self.box_height_hide)
        else:
            pass

    #def fade_in(self):
    #    eff = QtWidgets.QGraphicsOpacityEffect()
    #    self.box.setGraphicsEffect(eff)
    #    ani = QtCore.QPropertyAnimation(eff, b"opacity")
    #    ani.setDuration(350)
    #    ani.setStartValue(0)
    #    ani.setEndValue(1)
    #    ani.setEasingCurve(QtCore.QEasingCurve.InBack)
    #    ani.start(QtCore.QPropertyAnimation.DeleteWhenStopped)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.startPos = event.pos()
            self.box_toggle = not self.box_toggle

    def mouseMoveEvent(self, event):
        if event.buttons() == QtCore.Qt.MouseButton.LeftButton:
            # relative movement
            delta = event.pos() - self.startPos
            # window limits
            lim = QtCore.QPoint(self.parent().size().width() - self.size().width(),
                                self.parent().size().height() - self.size().height())
            # new temporary position
            _pos = self.pos() + delta
            # x small
            if _pos.x() < 0:
                delta.setX(-self.pos().x())
            # x large
            if (lim - _pos).x() < 0:
                delta.setX(lim.x() - self.pos().x())
            # y small
            if _pos.y() < 0:
                delta.setY(-self.pos().y())
            # y large
            if (lim - _pos).y() < 0:
                delta.setY(lim.y() - self.pos().y())
            # move window to new position
            self.move(self.pos() + delta)
            # keep the box open after dragging
            self.box_toggle = True

def main():
    pg.setConfigOptions(background='w', antialias=True)
    app = QtWidgets.QApplication(sys.argv)
    main = MainWindow()
    main.show()
    sys.exit(app.exec())
    
if __name__ == '__main__':
    main()
