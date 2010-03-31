#!/usr/bin/env python
import sys
import os
import xml.sax.handler
import xml.sax
from optparse import OptionParser

COLORS = [
	"#1b9e77",
	"#d95f02",
	"#7570b3",
	"#e7298a",
	"#66a61e",
	"#e6ab02",
	"#a6761d",
	"#666666",
	"#8dd3c7",
	"#bebada",
	"#fb8072",
	"#80b1d3",
	"#fdb462",
	"#b3de69",
	"#fccde5",
	"#d9d9d9",
	"#bc80bd",
	"#ccebc5",
	"#ffed6f",
	"#ffffb3"
]

class Job:
	def __init__(self):
		self.id = None
		self.name = None
		self.level = 0
		self.parents = []
		self.children = []

class DAXHandler(xml.sax.handler.ContentHandler):
	"""
	This is a DAX file parser
	"""
	def __init__(self):
		self.dag = {}
		
	def startElement(self, name, attrs):
		if name == "job":
			job = Job()
			job.id = attrs.get("id")
			if job.id is None:
				raise Exception("Invalid DAX: attribute 'id' missing")
			job.name = attrs.get("name")
			if job.name is None:
				job.name = job.id[0:8]
			self.dag[job.id] = job
		elif name == "child":
			self.lastchild = attrs.get("ref")
		elif name == "parent":
			if self.lastchild is None:
				raise Exception("Invalid DAX: <parent> outside <child>")
			pid = attrs.get("ref")
			child = self.dag[self.lastchild]
			parent = self.dag[pid]
			child.parents.append(parent)
			parent.children.append(child)
			
	def endElement(self, name):
		if name == "child":
			self.lastchild = None

def parse_daxfile(fname):
	"""
	Parse DAG from a Pegasus DAX file.
	"""
	handler = DAXHandler()
	parser = xml.sax.make_parser()
	parser.setContentHandler(handler)
	f = open(fname,"r")
	parser.parse(f)
	f.close()
	return handler.dag
	
def xform_name(xform):
	"""
	Get the name part of a transformation. Format could be:
	  namespace::name:version
	  namespace::name
	  name:version
	  name
	"""
	rec = xform.split(':')
	if len(rec) == 2: return rec[0]
	elif len(rec) > 2: return rec[2]
	else: return xform
	
def parse_xform_name(path):
	"""
	Parse the transformation name from a submit script. Usually the
	transformation is in a special classad called '+pegasus_wf_xformation'.
	For special pegasus jobs (create_dir, etc.) set the name manually.
	"""
	# Handle special cases
	fname = os.path.basename(path)
	if fname.startswith("create_dir_"): return "create_dir"
	if fname.startswith("stage_in_"): return "stage_in"
	if fname.startswith("stage_out_"): return "stage_out"
	if fname.startswith("stage_inter_"): return "stage_inter"
	if fname.startswith("stage_worker_"): return "stage_worker"
	if fname.startswith("register_"): return "register"
	if fname.startswith("clean_up_"): return "clean_up"
	
	# Get it from the submit file
	f = open(path,'r')
	for line in f.readlines():
		if '+pegasus_wf_xformation' in line:
			full = line.split('"')[1]
			return xform_name(full)
	return None
	
def parse_dagfile(fname):
	"""
	Parse a DAG from a dagfile.
	"""
	dagdir = os.path.dirname(fname)
	dag = {}
	lastchild = None
	f = open(fname,'r')
	for line in f.readlines():
		line = line.strip()
		if line.startswith("JOB"):
			rec = line.split()
			job = Job()
			if len(rec) < 3:
				raise Exception("Invalid line:",line)
			job.id = rec[1] # Job id
			subfile = rec[2] # submit script
			if not os.path.isabs(subfile):
				subfile = os.path.join(dagdir,subfile)
			if os.path.isfile(subfile):
				# Try to get it from the file if it exists
				job.name = parse_xform_name(subfile)
			if job.name is None:
				# Otherwise just use the first 8 characters
				job.name = job.id[0:8]
			dag[job.id] = job
		elif line.startswith("PARENT"):
			rec = line.split()
			if len(rec) < 4:
				raise Exception("Invalid line:",line)
			p = dag[rec[1]]
			c = dag[rec[3]]
			p.children.append(c)
			c.parents.append(p)
	f.close()
	
	return dag

def remove_xforms(dag, xforms):
	"""
	Remove transformations in the DAG by name
	"""
	if len(xforms) == 0:
		return
	for id in dag.keys():
		job = dag[id]
		if job.name in xforms:
			print "Removing %s" % job.id
			for p in job.parents:
				p.children.remove(job)
			for c in job.children:
				c.parents.remove(job)
			del dag[id]
			
def inv_reachable(a, b):
	"""
	Is a reachable from b using reverse edges? Reverse edges are
	used because it is a little more efficient than forward edges
	assuming that a node is more likely to have children than
	parents. Does a BFS using the child->parent edges instead of the
	parent->child edges.
	"""
	fifo = [a]
	while len(fifo) > 0:
		n = fifo.pop()
		for p in n.parents:
			if p == b: return True
			fifo.append(p)
	return False
			
def simplify(dag):
	"""
	Simplify a DAG by removing redundant edges. Redundant edges are edges
	that go from a grandparent to a grandchild. In other words, they are
	edges that, if removed, do not change the dependencies in the workflow.
	We want to remove these because they clutter up the diagram and make
	it hard to read.
	"""
	# Find roots
	roots = []
	for id in dag:
		j = dag[id]
		if len(j.parents) == 0:
			roots.append(j)
	
	# Assign surrogate root
	root = Job()
	root.id = 'root'
	root.name = 'root'
	root.level = 0
	for j in roots:
		root.children.append(j)
		
	# Label all levels of the workflow (BFS)
	fifo = [root]
	while len(fifo) > 0:
		n = fifo.pop()
		for c in n.children:
			fifo.append(c)
			c.level = max(c.level, n.level + 1)

	# Eliminate any redundant edges (BFS)
	fifo = [root]
	while len(fifo) > 0:
		n = fifo.pop()
		children = n.children[:]
		for c in children:
			fifo.append(c)
			dist = c.level - n.level
			if dist > 1:
				c.parents.remove(n)
				if inv_reachable(c, n):
					sys.stderr.write(
						"Removing redunant edge: %s -> %s\n" % 
						(n.id, c.id))
					n.children.remove(c)
				else:
					c.parents.append(n)
		
	return dag
	
def emit_dot(dag, use_xforms=False, outfile="/dev/stdout"):
	"""
	Write a DOT-formatted diagram.
	use_xforms: Use transformation names instead of job names
	outfile: The file name to write the diagam out to.
	"""
	next_color = 0  # Keep track of next color
	xforms = {} # Keep track of transformation names to assign colors
	
	out = open(outfile,'w')
	
	out.write("""digraph dag {
	size="8.0,10.0"
	ratio=fill
	node [shape=ellipse, style=filled]
	edge [arrowhead=normal, arrowsize=1.0]
	\n""")
	
	for id in dag:
		j = dag[id]
		j.id = j.id.replace("-","_")
		if use_xforms:
			label = j.name
		else:
			label = j.id
		if j.name not in xforms:
			xforms[j.name] = next_color
			next_color += 1
		color = xforms[j.name]
		out.write('\t%s [color="%s",label="%s"]\n' % (j.id,COLORS[color],label))
		
	out.write('\n')
	
	for id in dag:
		j = dag[id]
		for c in j.children:
			out.write('\t"%s" -> "%s"\n' % (j.id,c.id))
			
	out.write("}\n")
	out.close()
	
def main():
	usage = "%prog [options] DAGFILE"
	description = """Parses DAGFILE and generates a DOT-formatted
graphical representation of the DAG. DAGFILE can be a Condor
DAGMan file, or a Pegasus DAX file."""
	parser = OptionParser(usage=usage,description=description)
	parser.add_option("-s", "--nosimplify", action="store_false",
		dest="simplify", default=True,
		help="Do not simplify the graph by removing redundant edges")
	parser.add_option("-n", "--names", action="store_false", 
		dest="xforms", default=True,
		help="Use job names as labels instead of transformation names")
	parser.add_option("-o", "--output", action="store",
		dest="outfile", metavar="FILE", default="/dev/stdout",
		help="Write output to FILE [default: stdout]")
	parser.add_option("-r", "--remove", action="append",
		dest="remove", metavar="XFORM", default=[],
		help="Remove jobs from the workflow by transformation name")

	(options, args) = parser.parse_args()
	
	if len(args) < 1:
		parser.error("Please specify DAGFILE")
		
	if len(args) > 1:
		parser.error("Invalid argument")
	
	dagfile = args[0]
	if dagfile.endswith(".dag"):
		dag = parse_dagfile(dagfile)
	else:
		dag = parse_daxfile(dagfile)
		
	remove_xforms(dag, options.remove)
		
	if options.simplify:
		dag = simplify(dag)
		
	emit_dot(dag, options.xforms, options.outfile)
	
if __name__ == '__main__':
	main()
