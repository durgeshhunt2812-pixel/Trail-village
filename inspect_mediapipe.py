import mediapipe as mp
print('version', getattr(mp, '__version__', 'unknown'))
print('module file', getattr(mp, '__file__', 'unknown'))
print('has solutions attr:', hasattr(mp, 'solutions'))
print('top-level names:', [n for n in dir(mp) if not n.startswith('_')][:200])
