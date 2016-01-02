import lasagna.io
import lasagna.config
import numpy as np
import glue.core
from lasagna import echo


luts = None
display_ranges = None

default_color = lambda: lasagna.config.j.java.awt.Color.GRAY

fiji_label = 1

class FijiViewer(object):

	@staticmethod
	def setup(self, axes):
		j = lasagna.config.j

		self.imp = lasagna.io.show_hyperstack(np.zeros((100,100)), title='glue')

		# make a key listener for selection events
		j.add_key_typed(make_selection_listener(key='`'), self.imp)
		self.displayed_file = None
		
	@staticmethod
	def plot_data(self, axes, source, y, contours, bounds, context):
		j = lasagna.config.j

		if y.size:
			axes.scatter(source, y, s=10, c='g', marker='.')
		
		# can recolor these as necessary
		bounds = bounds.categories[bounds.astype(int)]
		self.contours = np.array([x + y[:2] for x,y in zip(contours.id._unhashable, bounds)])

		self.files = source.categories[source.astype(int)]

		if self.imp.getOverlay():
			self.imp.getOverlay().setStrokeColor(j.java.awt.Color.GRAY)

	@staticmethod
	def plot_subset(self, axes, source, y, contours, context, style):
		j = lasagna.config.j

		overlay = self.imp.getOverlay()
		# decide on a file
		if not source.size:
			# reset overlay
			# self.imp.setOverlay(j.ij.gui.Overlay())
			pass
		
		else:

			# take first selected file, only update display if changed
			to_show = source.astype(int)[0]
			file_to_show = source.categories[to_show]

			att_index = np.where(self.files == file_to_show)
			if file_to_show != self.displayed_file:
				data = lasagna.io.read_stack(file_to_show)
				self.imp = lasagna.io.show_hyperstack(data, imp=self.imp, 
									luts=luts, display_ranges=display_ranges)

				self.displayed_file = file_to_show
	
				# overlay all contours for the new file
				all_contours = self.contours[att_index]
				packed = [(1 + contour).T.tolist() for contour in all_contours]
				j.overlay_contours(packed)
				self.imp.getOverlay().setStrokeColor(default_color())

			overlay = self.imp.getOverlay()
			color = j.java.awt.Color.decode(style.color)
			# only show contours that apply to this file
			rois = np.where(np.in1d(att_index, contours))[0]
			lasagna.config.selection = np.intersect1d(contours, att_index)
			lasagna.config.self_contours = self.contours
			lasagna.config.a = att_index
			lasagna.config.b = contours
			lasagna.config.imp = self.imp
			# lasagna.config.selection2 = np.where(np.in1d(np.array(self.contours[att_index]), np.array(contours)))[0]
			# j.set_overlay_contours_color(np.intersect1d(contours, att_index), self.imp, color)
			j.set_overlay_contours_color(rois, self.imp, color)
			self.imp.updateAndDraw()

			# reset contour when mpl artist removed
			if not y.size:
				artist = axes.scatter([], [])
			else:
				artist = axes.scatter(source, y, s=100, c=style.color, marker='.', alpha=0.5)
				rois = np.intersect1d(contours, att_index)
				rois = np.where(np.in1d(att_index, contours))[0]
				def do_first(g, imp=self.imp, rois=rois):
					def wrapped(*args, **kwargs):
						# if overlay is gone (new file loaded), skip
						if imp.getOverlay():
							lasagna.config.rois = rois
							j.set_overlay_contours_color(rois, 
														 imp, default_color())
						return g(*args, **kwargs)
					return wrapped
				artist.remove = do_first(artist.remove)


	@staticmethod
	def make_selector(self, roi, source, y):
		state = glue.core.subset.RoiSubsetState()
		state.roi = roi
		# selections on image will always be along x and y axes
		# coud imagine propagating more complex selection criteria from image (e.g.,
		# magic wand for nearest neighbors on barcode simplex)
		state.xatt = source.id
		state.yatt = y.id
		return state
		# update selection, bypassing callback


def update_selection(selection):
	"""Assumes first dataset contains 'x' and 'y' components.
	Selection consists of (xmin, xmax, ymin, ymax)
	"""
	
	xmin, xmax, ymin, ymax = selection
	roi = glue.core.roi.RectangularROI(xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax)
	xatt = lasagna.config.app.data_collection[0].data.find_component_id('x')
	yatt = lasagna.config.app.data_collection[0].data.find_component_id('y')
	subset_state = glue.core.subset.RoiSubsetState(xatt=xatt , yatt=yatt, roi=roi)
	
	data_collection = lasagna.config.app.data_collection

	# if no subset groups selected, make a new one
	layers = (lasagna.config.app.centralWidget()
					 .layerWidget.layerTree.selected_layers())
	subset_groups = [s for s in layers if isinstance(s, glue.core.subset_group.SubsetGroup)]
	if len(subset_groups) == 0:
		global fiji_label
		new_label = 'Fiji %d' % fiji_label
		fiji_label += 1
		data_collection.new_subset_group(label=new_label, subset_state=subset_state)
	else:
		edit_mode = glue.core.edit_subset_mode.EditSubsetMode()
		edit_mode.update(data_collection, subset_state)


def make_selection_listener(key='u'):
	def selection_listener(event):
		if event.getKeyChar().lower() == key:
			imp = event.getSource().getImage()
			roi = imp.getRoi()
			if roi:
				if roi.getTypeAsString() == 'Rectangle':
					rect = roi.getBounds()
					selection = (rect.getMinX(), rect.getMaxX(), 
								 rect.getMinY(), rect.getMaxY())
					update_selection(selection)
	return selection_listener

	
				



