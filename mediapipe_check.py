import mediapipe as mp
print('version:', getattr(mp, '__version__', 'unknown'))
print('has solutions:', hasattr(mp, 'solutions'))
print('module path:', mp.__file__)
print('dir subset:', [n for n in dir(mp) if 'solutions' in n.lower() or 'drawing' in n.lower()])
