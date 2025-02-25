# import pyan
# from IPython.display import HTML
# HTML(pyan.create_callgraph(filenames="C:/Users/fov/PycharmProjects/PD_Remaster*.py", format="html"))
#
import os

print(os.popen('code2flow ./ -o diagram.png').read())


# print(os.popen('pyreverse -ASmy -c pylint.checkers.classes.ClassChecker pylint').read())

# print(os.popen('pyan3 ./*.py --uses --no-defines --colored --grouped --annotated --dot >callgraph.dot').read())
# print(os.popen('dot -Tpng callgraph.dot -o callgraph.png').read())