from __future__ import annotations
import math

def _mul(a,b):return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
def _rigid_inv(t):
    r=[[t[i][j] for j in range(3)] for i in range(3)];p=[t[i][3] for i in range(3)];o=[[0.0]*4 for _ in range(4)]
    for i in range(3):
        for j in range(3):o[i][j]=r[j][i]
        o[i][3]=-sum(r[j][i]*p[j] for j in range(3))
    o[3][3]=1.0;return o
def pose_matrix(p):
    x,y,z,w,pit,r=[float(v) for v in p];w,pit,r=map(math.radians,(w,pit,r));cw,sw=math.cos(w),math.sin(w);cp,sp=math.cos(pit),math.sin(pit);cr,sr=math.cos(r),math.sin(r)
    # FANUC-style W/P/R: X then Y then Z intrinsic, equivalent Rz(R)*Ry(P)*Rx(W).
    rot=[[cr*cp,cr*sp*sw-sr*cw,cr*sp*cw+sr*sw],[sr*cp,sr*sp*sw+cr*cw,sr*sp*cw-cr*sw],[-sp,cp*sw,cp*cw]]
    return [[rot[0][0],rot[0][1],rot[0][2],x],[rot[1][0],rot[1][1],rot[1][2],y],[rot[2][0],rot[2][1],rot[2][2],z],[0,0,0,1]]
def matrix_pose(t):
    p=math.asin(max(-1,min(1,-t[2][0])));cp=math.cos(p)
    if abs(cp)>1e-9:w=math.atan2(t[2][1],t[2][2]);r=math.atan2(t[1][0],t[0][0])
    else:w=0.0;r=math.atan2(-t[0][1],t[1][1])
    return [t[0][3],t[1][3],t[2][3],math.degrees(w),math.degrees(p),math.degrees(r)]
def transform_pose_between_frames(point_pose,old_frame_pose,new_frame_pose):
    world=_mul(pose_matrix(old_frame_pose),pose_matrix(point_pose));return matrix_pose(_mul(_rigid_inv(pose_matrix(new_frame_pose)),world))
