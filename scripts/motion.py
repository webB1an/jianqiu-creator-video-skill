"""Frame-sampled editorial moves, independent of canvas size or frame rate."""
import math

def ease(t):
    t = min(1.,max(0.,t))
    return t*t*t*(t*(t*6-15)+10)

def progress(seconds, start, duration):
    return ease((seconds-start)/duration)

def between(a,b,t):
    return a+(b-a)*t

def pose(start,end,t):
    return tuple(between(a,b,t) for a,b in zip(start,end))

def branch(start,end,progress):
    """Return a partially traced horizontal cubic, sampled at constant arc length."""
    x,y=start
    u,v=end
    points=[]
    for index in range(65):
        t=index/64
        s=1-t
        points.append((s**3*x+3*s*s*t*between(x,u,.55)+3*s*t*t*between(x,u,.45)+t**3*u,
                       s**3*y+3*s*s*t*y+3*s*t*t*v+t**3*v))
    lengths=[math.dist(a,b) for a,b in zip(points,points[1:])]
    remaining=min(1,max(0,progress))*sum(lengths)
    path=[points[0]]
    for a,b,length in zip(points,points[1:],lengths):
        if length>remaining:
            path.append(pose(a,b,remaining/length))
            break
        path.append(b)
        remaining-=length
    return path
