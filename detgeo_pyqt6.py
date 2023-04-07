import os, sys, json
import numpy as np
import pyqtgraph as pg
from PyQt6 import QtWidgets, QtCore, QtGui
from contourpy import contour_generator
from pyFAI import calibrant

#################################################
# - stylesheet qframe (?)
# - segmented contour lines are not
#   displayed properly, only one segment
#   is drawn (we pick the last).
#   this happens when the grid is not large
#   enough to host the full contour.
#   To compensate, the grid gets a multiplier
#   to reduce segmentation, multiplier = 1.5
# - check causality
# - find copy paste bugs from matplotlib version
#################################################

class MainWindow(pg.QtWidgets.QMainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        file_dump = os.path.join(os.path.dirname(__file__), 'settings.json')
        # save parameters to file
        # - save_default: overwrite existing file with defaults
        # - force_write: overwrite existing file after load
        self.init_par(file_dump, save_default=False, force_write=True)

        # import pyFAI if reference contours are enabled
        self.geo.pyFAI_calibrant = calibrant

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
        self.ax = pg.plot()
        self.layout.addWidget(self.ax)

        # translate unit for plot title
        self.geo.unit_names = ['2\U0001D6F3 [\u00B0]', 'd [\u212B\u207B\u00B9]', 'q [\u212B]', 'sin(\U0001D6F3)/\U0001D706 [\u212B]']
        if self.geo.unit >= len(self.geo.unit_names):
            print(f'Error: Valid geo.unit range is from 0 to {len(self.geo.unit_names)-1}, geo.unit={self.geo.unit}')
            raise SystemExit
        
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

    def add_unit_label(self):
        font = QtGui.QFont()
        font.setPixelSize(self.plo.unit_label_size)
        self.unit_label = pg.TextItem(anchor=(0.0,0.0), color=self.plo.unit_label_color, fill=self.plo.unit_label_fill)
        self.unit_label.setText(self.geo.unit_names[self.geo.unit])
        self.unit_label.setFont(font)
        self.ax.addItem(self.unit_label)
        self.unit_label.setPos(-self.plo.xdim, self.plo.ydim)

    def init_menus(self):
        menuBar = self.menuBar()
        self.setMenuBar(menuBar)

        menu_det = menuBar.addMenu('Detector')
        for d in self.detectors:
            d_menu = QtWidgets.QMenu(d, self)
            d_menu.setStatusTip('')
            menu_det.addMenu(d_menu)
            for s in self.detectors[d]['size']:
                det_action = QtGui.QAction(s, self)
                self.set_menu_action(det_action, self.change_detector, d, s)
                d_menu.addAction(det_action)
        
        menu_ref = menuBar.addMenu('Reference')
        for ref_name in self.geo.ref_pyFAI:
            ref_action = QtGui.QAction(ref_name, self)
            self.set_menu_action(ref_action, self.change_reference, ref_name)
            menu_ref.addAction(ref_action)

        menu_unit = menuBar.addMenu('Units')
        for unit_index, unit_name in enumerate(self.geo.unit_names):
            unit_action = QtGui.QAction(unit_name, self)
            self.set_menu_action(unit_action, self.change_units, unit_index)
            menu_unit.addAction(unit_action)

    def set_menu_action(self, action, target, *args):
        action.triggered.connect(lambda: target(*args))

    def change_reference(self, ref_name):
        self.geo.reference = ref_name
        self.get_reference()
        self.draw_reference()

    def change_detector(self, det_name, det_size):
        self.det = self.get_specs_det(self.detectors, det_name, det_size)
        self.ax.clear()
        self.init_screen()
        self.sliderWidget.center_frame()

    def change_units(self, unit_index):
        self.geo.unit = unit_index
        self.unit_label.setText(self.geo.unit_names[unit_index])
        self.draw_contours()

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
    
        self.get_colormap()
        self.get_reference()

        # container for contour lines
        self.plo.contours = {'exp':[], 'ref':[], 'labels':[]}
        # add empty plot per contour line
        for _ in range(self.plo.cont_tth_num):
            self.plo.contours['exp'].append(self.ax.plot(useCache=True, pxMode=True))
            temp_label = pg.TextItem(anchor=(0.5,0.5), fill=pg.mkBrush('w'))
            self.plo.contours['labels'].append(temp_label)
            self.ax.addItem(temp_label)
        
        # add empty plot per reference contour line
        for _ in range(len(self.plo.cont_ref_dsp)):
            self.plo.contours['ref'].append(self.ax.plot(useCache=True, pxMode=True))

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
        self.resize(int(self.plo.plot_size*self.plo.xdim/self.plo.ydim), self.plo.plot_size)

        # scale contour grid to detector size
        multiplier = 1.5
        self.plo.cont_grid_max = int(np.ceil(max(self.plo.xdim*multiplier, self.plo.ydim*multiplier)))
        
        # generate contour levels
        self.plo.cont_levels = np.linspace(self.plo.cont_tth_min, self.plo.cont_tth_max, self.plo.cont_tth_num)

        # name the window
        self.setWindowTitle(self.det.name)

        # build detector modules
        self.build_detector()

        # add unit label
        self.add_unit_label()

        # create cones and draw contour lines
        self.draw_contours()
        self.draw_reference()
        
    def get_colormap(self):
        # figure out the color of the buttons and slider handles
        # get colormap
        self.plo.cont_cmap = pg.colormap.get(self.plo.cont_geom_cmap_name)
        try:
            # try to derive color from colormap
            self.plo.plot_handle_color = self.plo.cont_cmap.map(self.plo.plot_color, mode='qcolor')
        except TypeError:
            # use color as defined by user
            self.plo.plot_handle_color = self.plo.plot_color
    
    def get_reference(self):
        # get contour lines f contours are already selected (index is not 0, not None)
        if self.geo.reference != 'None':
            # get the d spacings for the calibrtant from pyFAI
            self.plo.cont_ref_dsp = np.array(self.geo.pyFAI_calibrant.get_calibrant(self.geo.reference).get_dSpacing()[:self.plo.cont_ref_num])
        else:
            self.plo.cont_ref_dsp = np.zeros(self.plo.cont_ref_num) -1

    def get_specs_geo(self):
        ######################
        # Setup the geometry #
        ######################
        geo = container()
        geo.det_type = 'Eiger2' # [str]  Pilatus3 / Eiger2
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
                                #          pick from list below
        # What standards should be available as reference
        # The d spacings will be imported from pyFAI
        geo.ref_pyFAI = ['None', 'LaB6', 'Si', 'CeO2']
        
        return geo

    def get_specs_plo(self):
        ################
        # Plot Details #
        ################
        plo = container()
        # - geometry contour section - 
        plo.cont_tth_min = 5                # [int]    Minimum 2-theta contour line
        plo.cont_tth_max = 120              # [int]    Maximum 2-theta contour line
        plo.cont_tth_num = 24               # [int]    Number of contour lines
        plo.cont_geom_cmark = 'o'           # [marker] Beam center marker (geometry)
        plo.cont_geom_csize = 6             # [int]    Beam center size (geometry)
        plo.cont_geom_alpha = 1.00          # [float]  Contour alpha (geometry)
        plo.cont_geom_lw = 4.0              # [float]  Contour linewidth
        plo.cont_geom_label = 8             # [int]    Contour label size
        plo.cont_geom_cmap_name = 'viridis' # [cmap]   Contour colormap (geometry)
        # - reference contour section - 
        plo.cont_reference = True           # [bool]   Plot reference contour lines
                                            #          e.g. a LaB6 standard
        plo.cont_ref_alpha = 0.25           # [float]  Reference contour alpha
        plo.cont_ref_color = 'gray'         # [color]  Reference contour color
        plo.cont_ref_lw = 5.0               # [float]  Reference contour linewidth
        plo.cont_ref_num = 48               # [int]    Number of reference contours
        # - module section - 
        plo.module_alpha = 0.20             # [float]  Detector module alpha
        plo.module_color = 'gray'           # [color]  Detector module color
        # - general section - 
        plo.cont_reso_min = 50              # [int]    Minimum contour steps
        plo.cont_reso_max = 500             # [int]    Maximum contour steps
        plo.plot_size = 768                 # [int]    Plot size, px
        plo.unit_label_size = 16            # [int]    Label size, px
        plo.unit_label_color = 'gray'       # [str]    Label color
        plo.unit_label_fill = 'white'       # [str]    Label fill color
        plo.plot_color = 0.35               # [float]  Button color from colormap (0.0 - 1.0)
                                            # [str]    Button color e.g. '#1f77b4'
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
        lmt.ener_min = 1.0   # [float] Energy minimum [keV]
        lmt.ener_max = 100.0 # [float] Energy maximum [keV]
        lmt.ener_stp = 1.0   # [float] Energy step size [keV]
        lmt.dist_min = 40.0  # [float] Distance minimum [mm]
        lmt.dist_max = 150.0 # [float] Distance maximum [mm]
        lmt.dist_stp = 1.0   # [float] Distance step size [mm]
        lmt.xoff_min = -50.0 # [float] Horizontal offset minimum [mm]
        lmt.xoff_max = 50.0  # [float] Horizontal offset maximum [mm]
        lmt.xoff_stp = 1.0   # [float] Horizontal offset step size [mm]
        lmt.yoff_min = 0.0   # [float] Vertical offset minimum [mm]
        lmt.yoff_max = 200.0 # [float] Vertical offset maximum [mm]
        lmt.yoff_stp = 1.0   # [float] Vertical offset step size [mm]
        lmt.rota_min = 0.0   # [float] Rotation minimum [deg]
        lmt.rota_max = 75.0  # [float] Rotation maximum [deg]
        lmt.rota_stp = 1.0   # [float] Rotation step size [deg]
        lmt.tilt_min = 0.0   # [float] Tilt minimum [deg]
        lmt.tilt_max = 45.0  # [float] Tilt maximum [deg]
        lmt.tilt_stp = 1.0   # [float] Tilt step size [deg]
        
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
        file_dump = os.path.join(os.path.dirname(__file__), 'detectors.json')
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
        # increase the the cone grid to allow more
        # contours to be drawn as the plane is tilted
        _comp_add = np.tan(np.deg2rad(self.geo.tilt))*self.geo.dist
        # update beam center
        self.plo.beam_center.setData([self.geo.xoff],[_comp_shift],
                                     symbol = self.plo.cont_geom_cmark,
                                     size = self.plo.cont_geom_csize,
                                     brush = pg.mkBrush(self.plo.cont_cmap.map(0, mode='qcolor')))
        for _n, _ttd in enumerate(self.plo.cont_levels):
            # current fraction for colormap
            _f = _n/len(self.plo.cont_levels)
            # convert theta in degrees to radians
            _ttr = np.deg2rad(_ttd)
            # calculate ratio of sample to detector distance (sdd)
            # and contour distance to beam center (cbc)
            # _rat = sdd/cbc = 1/tan(2-theta)
            # this is used to scale the cones Z dimension
            _rat = 1/np.tan(_ttr)
            # apply the min/max grid resolution
            _grd_res = max(min(int(self.plo.cont_reso_min*_rat), self.plo.cont_reso_max), self.plo.cont_reso_min)
            # prepare the grid for the cones/contours
            # adjust the resolution using i (-> plo.cont_levels),
            # as smaller cones/contours (large i) need higher sampling
            # but make sure the sampling rate doesn't fall below the
            # user set plo.cont_reso_min value and plo.cont_reso_max
            # prevents large numbers that will take seconds to draw
            # the grid position needs to adjusted upon change of geometry (y, vertical)
            # the center needs to be shifted by _geo_offset to make sure all contour lines are drawn
            _x1 = np.linspace(-self.plo.cont_grid_max + _comp_shift, self.plo.cont_grid_max - _comp_shift + _comp_add, _grd_res)
            # the grid position needs to adjusted upon change of geometry (x, horizontal)
            # the center needs to be shifted by geo.xoff to make sure all contour lines are drawn
            _x2 = np.linspace(-self.plo.cont_grid_max - self.geo.xoff, self.plo.cont_grid_max - self.geo.xoff, _grd_res)
            # Conversion factor keV to Angstrom: 12.398
            # sin(t)/l: np.sin(Theta) / lambda -> (12.398/geo_energy)
            _stl = np.sin(_ttr/2)/(12.398/self.geo.ener)
            # d-spacing: l = 2 d sin(t) -> 1/2(sin(t)/l)
            _dsp = 1/(2*_stl)
            # prepare the values in the different units / labels
            _units = {0:np.rad2deg(_ttr), 1:_dsp, 2:_stl*4*np.pi, 3:_stl}
            # draw additional contours for normal incidence geometry
            X0, Y0 = np.meshgrid(_x1,_x2)
            Z0 = np.sqrt(X0**2+Y0**2)*_rat
            X,Y,Z = self.calc_cone(X0, Y0, Z0, self.geo.rota, self.geo.tilt, self.geo.xoff, self.geo.yoff, self.geo.dist)
            # don't draw contour lines that are out of bounds
            # make sure Z is large enough to draw the contour
            cont_gen = contour_generator(x=X, y=Y, z=Z)
            if np.max(Z) >= self.geo.dist:
                cline = cont_gen.lines(self.geo.dist)[-1]
                #if len(cont_gen.lines(self.geo.dist)) > 1:
                #    print(cont_gen.lines(self.geo.dist))
                #cline = np.vstack(cont_gen.lines(self.geo.dist))
                self.plo.contours['exp'][_n].setData(cline, pen=pg.mkPen(self.plo.cont_cmap.map(_f, mode='qcolor'), width=self.plo.cont_geom_lw))
                # label contour lines
                self.plo.contours['labels'][_n].setText(f'{np.round(_units[self.geo.unit],2):.2f}', color=self.plo.cont_cmap.map(_f, mode='qcolor'))
                self.plo.contours['labels'][_n].setPos(self.geo.xoff,np.max(cline[:,1]))
                self.plo.contours['labels'][_n].setVisible(True)
            else:
                self.plo.contours['labels'][_n].setVisible(False)
                self.plo.contours['exp'][_n].setData([])
                self.plo.contours['exp'][_n].clear()
    
    def draw_reference(self):
        # calculate the offset of the contours resulting from yoff and rotation
        # shift the grid to draw the cones, to make sure the contours are drawn
        # within the visible area
        _comp_shift = -(self.geo.yoff + np.tan(np.deg2rad(self.geo.rota))*self.geo.dist)
        # increase the the cone grid to allow more
        # contours to be drawn as the plane is tilted
        _comp_add = np.tan(np.deg2rad(self.geo.tilt))*self.geo.dist
        # plot reference contour lines
        # satndard contour lines are to be drawn
        for _n,_d in enumerate(self.plo.cont_ref_dsp):
            # lambda = 2 * d * sin(theta)
            # 2-theta = 2 * (lambda / 2*d)
            # lambda -> (12.398/geo_energy)
            lambda_d = (12.398/self.geo.ener) / (2*_d)
            if lambda_d > 1.0:
                continue
            _ttr = 2 * np.arcsin(lambda_d)
            # calculate ratio of sample to detector distance (sdd)
            # and contour distance to beam center (cbc)
            # _rat = sdd/cbc = 1/tan(2-theta)
            # this is used to scale the cones Z dimension
            _rat = 1/np.tan(_ttr)
            # apply the min/max grid resolution
            _grd_res = max(min(int(self.plo.cont_reso_min*_rat), self.plo.cont_reso_max), self.plo.cont_reso_min)
            # the grid position needs to adjusted upon change of geometry (y, vertical)
            # the center needs to be shifted by _geo_offset to make sure all contour lines are drawn
            _x1 = np.linspace(-self.plo.cont_grid_max + _comp_shift, self.plo.cont_grid_max - _comp_shift + _comp_add, _grd_res)
            # the grid position needs to adjusted upon change of geometry (x, horizontal)
            # the center needs to be shifted by geo.xoff to make sure all contour lines are drawn
            _x2 = np.linspace(-self.plo.cont_grid_max - self.geo.xoff, self.plo.cont_grid_max - self.geo.xoff, _grd_res)
            # draw contours for the tilted/rotated/moved geometry
            # use the offset adjusted value x1 to prepare the grid
            X0, Y0 = np.meshgrid(_x1,_x2)
            Z0 = np.sqrt(X0**2+Y0**2)*_rat
            X,Y,Z = self.calc_cone(X0, Y0, Z0, self.geo.rota, self.geo.tilt, self.geo.xoff, self.geo.yoff, self.geo.dist)
            # make sure Z is large enough to draw the contour
            cont_gen = contour_generator(x=X, y=Y, z=Z)
            if np.max(Z) >= self.geo.dist:
                cline = cont_gen.lines(self.geo.dist)[-1]
                self.plo.contours['ref'][_n].setData(cline, pen=pg.mkPen(self.plo.cont_ref_color, width=self.plo.cont_ref_lw))
                self.plo.contours['ref'][_n].setAlpha(self.plo.cont_ref_alpha, False)
            else:
                self.plo.contours['ref'][_n].setData([])
                self.plo.contours['ref'][_n].clear()

    def calc_cone(self, X, Y, Z, rota, tilt, xoff, yoff, dist):
        # combined rotation, tilt 'movement' is compensated
        a = np.deg2rad(tilt) + np.deg2rad(rota)
        # rotate the sample around y
        t = np.transpose(np.array([X,Y,Z]), (1,2,0))
        # rotation matrix
        m = [[np.cos(a), 0, np.sin(a)],[0,1,0],[-np.sin(a), 0, np.cos(a)]]
        # apply rotation
        X,Y,Z = np.transpose(np.dot(t, m), (2,0,1))
        # compensate for tilt not rotating
        # - revert the travel distance
        comp = np.deg2rad(tilt) * dist
        return Y+xoff,X+comp-yoff,Z

    def update_screen(self, nam, val):
        if nam == 'dist':
            self.geo.dist = float(val)
        elif nam == 'rota':
            self.geo.rota = float(val)
        elif nam == 'tilt':
            self.geo.tilt = float(val)
        elif nam == 'yoff':
            self.geo.yoff = float(val)
        elif nam == 'xoff':
            self.geo.xoff = float(val)
        elif nam == 'unit':
            self.geo.unit = int(val)
        elif nam == 'ener':
            self.geo.ener = float(val)
        elif nam == 'ref':
            self.geo.reference = str(val)
        # re-calculate cones and re-draw contours
        self.draw_contours()
        # draw reference contours
        if self.geo.reference != 'None':
            # get the d spacings for the calibrtant from pyFAI
            self.plo.cont_ref_dsp = np.array(self.geo.pyFAI_calibrant.get_calibrant(self.geo.reference).get_dSpacing()[:self.plo.cont_ref_num])
            self.draw_reference()

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
            sli_ener, lab_ener_name, lab_ener_value = self.add_slider(grid, 'Energy\n[keV]', 'Energy [keV] ', _idx)
            sli_ener.valueChanged.connect(lambda: parent.update_screen('ener', sli_ener.value()))
            sli_ener.valueChanged.connect(lambda: self.update_slider(lab_ener_value, sli_ener.value()))
            self.set_slider(sli_ener, self.geo.ener, lmt.ener_min, lmt.ener_max, lmt.ener_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1
        if plo.action_dist:
            sli_dist, lab_dist_name, lab_dist_value = self.add_slider(grid, 'Distance\n[mm]', 'Distance [mm] ', _idx)
            sli_dist.valueChanged.connect(lambda: parent.update_screen('dist', sli_dist.value()))
            sli_dist.valueChanged.connect(lambda: self.update_slider(lab_dist_value, sli_dist.value()))
            self.set_slider(sli_dist, self.geo.dist, lmt.dist_min, lmt.dist_max, lmt.dist_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1
        if plo.action_yoff:
            sli_yoff, lab_yoff_name, lab_yoff_value = self.add_slider(grid, 'Y offset\n[mm]', 'Y offset [mm] ', _idx)
            sli_yoff.valueChanged.connect(lambda: parent.update_screen('yoff', sli_yoff.value()))
            sli_yoff.valueChanged.connect(lambda: self.update_slider(lab_yoff_value, sli_yoff.value()))
            self.set_slider(sli_yoff, self.geo.yoff, lmt.yoff_min, lmt.yoff_max, lmt.yoff_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1
        if plo.action_xoff:
            sli_xoff, lab_xoff_name, lab_xoff_value = self.add_slider(grid, 'X offset\n[mm]', 'X offset [mm] ', _idx)
            sli_xoff.valueChanged.connect(lambda: parent.update_screen('xoff', sli_xoff.value()))
            sli_xoff.valueChanged.connect(lambda: self.update_slider(lab_xoff_value, sli_xoff.value()))
            self.set_slider(sli_xoff, self.geo.xoff, lmt.xoff_min, lmt.xoff_max, lmt.xoff_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1
        if plo.action_tilt:
            sli_tilt, lab_tilt_name, lab_tilt_value = self.add_slider(grid, 'Tilt\n[˚]', 'Tilt [˚] ', _idx)
            sli_tilt.valueChanged.connect(lambda: parent.update_screen('tilt', sli_tilt.value()))
            sli_tilt.valueChanged.connect(lambda: self.update_slider(lab_tilt_value, sli_tilt.value()))
            self.set_slider(sli_tilt, self.geo.tilt, lmt.tilt_min, lmt.tilt_max, lmt.tilt_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1
        if plo.action_rota:
            sli_rota, lab_rota_name, lab_rota_value = self.add_slider(grid, 'Rotation\n[˚]', 'Rotation [˚] ', _idx)
            sli_rota.valueChanged.connect(lambda: parent.update_screen('rota', sli_rota.value()))
            sli_rota.valueChanged.connect(lambda: self.update_slider(lab_rota_value, sli_rota.value()))
            self.set_slider(sli_rota, self.geo.rota, lmt.rota_min, lmt.rota_max, lmt.rota_stp)
            self.box_width_dynamic += self.box_width_add
            _idx += 1

        self.resize(self.box_width_dynamic, self.box_height_hide)
        self.center_frame()

    def center_frame(self):
        self.move(int((self.parent().size().width()-self.box_width_dynamic)/2),0)

    def update_slider(self, label, value):
        label.setText(str(int(value)))

    def add_slider(self, layout, label, hint, idx):
        label_name = QtWidgets.QLabel(label)
        label_name.setToolTip(hint)
        label_name.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label_name, 0, idx, QtCore.Qt.AlignmentFlag.AlignCenter)
        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Vertical)
        slider.setValue(999)
        slider.setToolTip(hint)
        layout.addWidget(slider, 1, idx, QtCore.Qt.AlignmentFlag.AlignHCenter)
        label_value = QtWidgets.QLabel()
        label_value.setToolTip(hint)
        label_value.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label_value, 2, idx, QtCore.Qt.AlignmentFlag.AlignCenter)
        return slider, label_name, label_value

    def set_slider(self, slider, val, vmin, vmax, step):
        slider.setRange(int(vmin), int(vmax))
        slider.setSingleStep(int(step))
        slider.setPageStep(int(step))
        slider.setValue(int(val))

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
            delta = event.pos() - self.startPos
            self.move(self.pos() + delta)
            self.box_toggle = True

def main():
    pg.setConfigOptions(background='w', antialias=True)
    app = QtWidgets.QApplication(sys.argv)
    main = MainWindow()
    main.show()
    sys.exit(app.exec())
    
if __name__ == '__main__':
    main()