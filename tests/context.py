import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                '..')))

os.environ['OPENAKUN_TESTING'] = '1'
import openakun.pages as pages
