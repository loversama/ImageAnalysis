#!/usr/bin/python

import cv2
import fileinput
import fnmatch
import fractions
import json
import math
from matplotlib import pyplot as plt
import numpy as np
import os.path
from progress.bar import Bar
import scipy.interpolate
import subprocess
import sys
import time

import geojson

from props import root, getNode
import props_json

from getchar import find_getch
import Camera
import Image

import ImageList
import Matcher
import Render
import transformations


class ProjectMgr():
    def __init__(self, project_dir, create=False):
        self.project_dir = project_dir
        self.cam = Camera.Camera()
        self.image_list = []
        self.matcher_params = { 'matcher': 'FLANN', # { FLANN or 'BF' }
                                'match-ratio': 0.75,
                                'filter': 'fundamental',
                                'image-fuzz': 40,
                                'feature-fuzz': 20 }

        # the following member variables need to be reviewed/organized
        self.ac3d_steps = 8
        self.render = Render.Render()
        self.dir_node = getNode('/config/directories', True)
        self.load( create )

    def set_defaults(self):
        self.cam.set_defaults() # camera defaults

    # project_dir is a new folder for all derived files
    def validate_project_dir(self, create_if_needed=True):
        if not os.path.exists(self.project_dir):
            if create_if_needed:
                print("Notice: creating project directory:", self.project_dir)
                os.makedirs(self.project_dir)
            else:
                print("Error: project dir doesn't exist: ", self.project_dir)
                return False

        # and make children directories
        meta_dir = os.path.join(self.project_dir, 'meta')
        if not os.path.exists(meta_dir):
            if create_if_needed:
                print("Notice: creating meta directory:", meta_dir)
                os.makedirs(meta_dir)
            else:
                print("Error: image dir doesn't exist:", meta_dir)
                return False
            
        # all is good
        return True

    # source_dir is the folder containing all the raw/original images.
    # The expected work flow is that we will import/scale all the
    # original images into our project folder leaving the original
    # image set completely untouched.
    def set_image_sources(self, image_dirs):
        for i, dir in enumerate(image_dirs):
            if dir == self.project_dir:
                print("Error: image source and project dirs must be different.")
                return
            if not os.path.exists(dir):
                print("Error: image source path does not exist:", dir)
            self.dir_node.setStringEnum('image_sources', i, dir)

    def save(self):
        # create a project dictionary and write it out as json
        if not os.path.exists(self.project_dir):
            print("Error: project doesn't exist:", self.project_dir)
            return

        project_file = os.path.join(self.project_dir, "config.json")
        config_node = getNode("/config", True)
        props_json.save(project_file, config_node)

    def load(self, create=True):
        if not self.validate_project_dir():
            return

        # load project configuration
        result = False
        project_file = os.path.join(self.project_dir, "config.json")
        config_node = getNode("/config", True)
        if os.path.isfile(project_file):
            if props_json.load(project_file, config_node):
                # fixme:
                # if 'matcher' in project_dict:
                #     self.matcher_params = project_dict['matcher']
                # root.pretty_print()
                result = True
            else:
                print("Notice: unable to load: ", project_file)
        else:
            print("Notice: project configuration doesn't exist:", project_file)
        if not result and create:
            print("Continuing with an empty project configuration")
            self.set_defaults()
        elif not result:
            print("aborting...")
            quit()

        #root.pretty_print()

    def load_images_info(self):
        # load image meta info
        result = False
        meta_dir = os.path.join(self.project_dir, 'meta')
        images_node = getNode("/images", True)

        for file in os.listdir(meta_dir):
            if fnmatch.fnmatch(file, '*.json'):
                name, ext = os.path.splitext(file)
                image_node = images_node.getChild(name, True)
                props_json.load(os.path.join(meta_dir, file), image_node)
        # images_node.pretty_print()
                
        # wipe image list (so we don't double load)
        self.image_list = []
        for name in images_node.getChildren():
            image = Image.Image(meta_dir, name)
            self.image_list.append( image )

        # make sure our matcher gets a copy of the image list
        self.render.setImageList(self.image_list)

    def load_features(self, descriptors=False):
        if descriptors:
            msg = 'Loading keypoints and descriptors:'
        else:
            msg = 'Loading keypoints:'
        bar = Bar(msg, max = len(self.image_list))
        for image in self.image_list:
            image.load_features()
            if descriptors:
                image.load_descriptors()
            bar.next()
        bar.finish()

    def load_match_pairs(self, extra_verbose=True):
        if extra_verbose:
            print("")
            print("ProjectMgr.load_match_pairs():")
            print("Notice: this routine is depricated for most purposes, unless")
            print("resetting the match state of the system back to the original")
            print("set of found matches.")
            time.sleep(2)
        bar = Bar('Loading keypoint (pair) matches:',
                  max = len(self.image_list))
        for image in self.image_list:
            image.load_matches()
            bar.next()
        bar.finish()

    # generate a n x n structure of image vs. image pair matches and
    # return it
    def generate_match_pairs(self, matches_direct):
        # generate skeleton structure
        result = []
        for i, i1 in enumerate(self.image_list):
            matches = []
            for j, i2 in enumerate(self.image_list):
                matches.append( [] )
            result.append(matches)
        # fill in the structure (a match = ned point followed by
        # image/feat-index, ...)
        for k, match in enumerate(matches_direct):
            #print match
            for p1 in match[1:]:
                for p2 in match[1:]:
                    if p1 == p2:
                        pass
                        #print 'skip self match'
                    else:
                        #print p1, 'vs', p2
                        i = p1[0]; j = p2[0]
                        result[i][j].append( [p1[1], p2[1], k] )
        #for i, i1 in enumerate(self.image_list):
        #    for j, i2 in enumerate(self.image_list):
        #        print 'a:', self.image_list[i].match_list[j]
        #        print 'b:', result[i][j]
        return result
                
    def save_images_info(self):
        # create a project dictionary and write it out as json
        if not os.path.exists(self.project_dir):
            print("Error: project doesn't exist:", self.project_dir)
            return

        meta_dir = os.path.join(self.project_dir, 'meta')
        images_node = getNode("/images", True)
        for name in images_node.getChildren():
            image_node = images_node.getChild(name, True)
            image_path = os.path.join(meta_dir, name + '.json')
            props_json.save(image_path, image_node)
            
    def set_matcher_params(self, mparams):
        self.matcher_params = mparams
        
    def detect_features(self, scale, show=False):
        if not show:
            bar = Bar('Detecting features:', max = len(self.image_list))
        for image in self.image_list:
            #print "detecting features and computing descriptors: " + image.name
            rgb = image.load_rgb()
            image.detect_features(rgb, scale)
            image.save_features()
            image.save_descriptors()
            image.save_matches()
            if show:
                result = image.show_features()
                if result == 27 or result == ord('q'):
                    break
            if not show:
                bar.next()
        if not show:
            bar.finish()

        self.save_images_info()

    def show_features_image(self, image):
        result = image.show_features()
        return result
        
    def show_features_images(self, name=None):
        for image in self.image_list:
            result = self.show_features_image(image)
            if result == 27 or result == ord('q'):
                break
                
    def findImageByName(self, name):
        for i in self.image_list:
            if i.name == name:
                return i
        return None
    
    def findIndexByName(self, name):
        for i, img in enumerate(self.image_list):
            if img.name == name:
                return i
        return None

    # compute a center reference location (lon, lat) for the group of
    # images.
    def compute_ned_reference_lla(self):
        # requires images to have their location computed/loaded
        lon_sum = 0.0
        lat_sum = 0.0
        count = 0
        images_node = getNode("/images", True)
        for name in images_node.getChildren():
            image_node = images_node.getChild(name, True)
            pose_node = image_node.getChild('aircraft_pose', True)
            if pose_node.hasChild('lon_deg') and pose_node.hasChild('lat_deg'):
                lon_sum += pose_node.getFloat('lon_deg')
                lat_sum += pose_node.getFloat('lat_deg')
                count += 1
        ned_node = getNode('/config/ned_reference', True)
        ned_node.setFloat('lat_deg', lat_sum / count)
        ned_node.setFloat('lon_deg', lon_sum / count)
        ned_node.setFloat('alt_m', 0.0)

    def undistort_uvlist(self, image, uv_orig):
        if len(uv_orig) == 0:
            return []
        # camera parameters
        dist_coeffs = np.array(self.cam.get_dist_coeffs())
        K = self.cam.get_K()
        # assemble the points in the proper format
        uv_raw = np.zeros((len(uv_orig),1,2), dtype=np.float32)
        for i, kp in enumerate(uv_orig):
            uv_raw[i][0] = (kp[0], kp[1])
        # do the actual undistort
        uv_new = cv2.undistortPoints(uv_raw, K, dist_coeffs, P=K)
        # return the results in an easier format
        result = []
        for i, uv in enumerate(uv_new):
            result.append(uv_new[i][0])
            #print "  orig = %s  undistort = %s" % (uv_raw[i][0], uv_new[i][0]
        return result
        
    # for each feature in each image, compute the undistorted pixel
    # location (from the calibrated distortion parameters)
    def undistort_keypoints(self, optimized=False):
        bar = Bar('Undistorting keypoints:', max = len(self.image_list))
        for image in self.image_list:
            if len(image.kp_list) == 0:
                continue
            K = self.cam.get_K(optimized)
            uv_raw = np.zeros((len(image.kp_list),1,2), dtype=np.float32)
            for i, kp in enumerate(image.kp_list):
                uv_raw[i][0] = (kp.pt[0], kp.pt[1])
            dist_coeffs = self.cam.get_dist_coeffs(optimized)
            uv_new = cv2.undistortPoints(uv_raw, K, np.array(dist_coeffs), P=K)
            image.uv_list = []
            for i, uv in enumerate(uv_new):
                image.uv_list.append(uv_new[i][0])
                # print("  orig = %s  undistort = %s" % (uv_raw[i][0], uv_new[i][0]))
            bar.next()
        bar.finish()
                
    # for each uv in the provided uv list, apply the distortion
    # formula to compute the original distorted value.
    def redistort(self, uv_list, K, dist_coeffs):
        fx = K[0,0]
        fy = K[1,1]
        cx = K[0,2]
        cy = K[1,2]
        k1, k2, p1, p2, k3 = dist_coeffs
        
        uv_distorted = []
        for pt in uv_list:
            x = (pt[0] - cx) / fx
            y = (pt[1] - cy) / fy

            # Compute radius^2
            r2 = x**2 + y**2
            r4, r6 = r2**2, r2**3

            # Compute tangential distortion
            dx = 2*p1*x*y + p2*(r2 + 2*x*x)
            dy = p1*(r2 + 2*y*y) + 2*p2*x*y

            # Compute radial factor
            Lr = 1.0 + k1*r2 + k2*r4 + k3*r6

            ud = Lr*x + dx
            vd = Lr*y + dy
            uv_distorted.append( [ud * fx + cx, vd * fy + cy] )
            
        return uv_distorted
    
    def compute_kp_usage(self, all=False):
        print("Determining feature usage in matching pairs...")
        # but they may have different scaling or other attributes important
        # during feature matching
        if all:
            for image in self.image_list:
                image.kp_used = np.ones(len(image.kp_list), np.bool_)
        else:
            for image in self.image_list:
                image.kp_used = np.zeros(len(image.kp_list), np.bool_)
            for i1 in self.image_list:
                for j, matches in enumerate(i1.match_list):
                    i2 = self.image_list[j]
                    for k, pair in enumerate(matches):
                        i1.kp_used[ pair[0] ] = True
                        i2.kp_used[ pair[1] ] = True
                    
    def compute_kp_usage_new(self, matches_direct):
        print("Determining feature usage in matching pairs...")
        for image in self.image_list:
            image.kp_used = np.zeros(len(image.kp_list), np.bool_)
        for match in matches_direct:
            for p in match[1:]:
                image = self.image_list[ p[0] ]
                image.kp_used[ p[1] ] = True
                    
    # project the list of (u, v) pixels from image space into camera
    # space, remap that to a vector in ned space (for camera
    # ypr=[0,0,0], and then transform that by the camera pose, returns
    # the vector from the camera, through the pixel, into ned space
    def projectVectors(self, IK, body2ned, cam2body, uv_list):
        proj_list = []
        for uv in uv_list:
            uvh = np.array([uv[0], uv[1], 1.0])
            proj = body2ned.dot(cam2body).dot(IK).dot(uvh)
            proj_norm = transformations.unit_vector(proj)
            proj_list.append(proj_norm)

        #for uv in uv_list:
        #    print "uv:", uv
        #    uvh = np.array([uv[0], uv[1], 1.0])
        #    print "cam vec=", transformations.unit_vector(IR.dot(IK).dot(uvh))
        return proj_list

    # project the (u, v) pixels for the specified image using the current
    # sba pose and write them to image.vec_list
    def projectVectorsImageSBA(self, IK, image):
        vec_list = []
        body2ned = image.get_body2ned_sba()
        cam2body = image.get_cam2body()
        for uv in image.uv_list:
            uvh = np.array([uv[0], uv[1], 1.0])
            proj = body2ned.dot(cam2body).dot(IK).dot(uvh)
            proj_norm = transformations.unit_vector(proj)
            vec_list.append(proj_norm)
        return vec_list

    # given a set of vectors in the ned frame, and a starting point.
    # Find the ground intersection point.  For any vectors which point into
    # the sky, return just the original reference/starting point.
    def intersectVectorsWithGroundPlane(self, pose_ned, ground_m, v_list):
        pt_list = []
        for v in v_list:
            # solve projection
            p = pose_ned
            if v[2] > 0.0:
                d_proj = -(pose_ned[2] + ground_m)
                factor = d_proj / v[2]
                n_proj = v[0] * factor
                e_proj = v[1] * factor
                p = [ pose_ned[0] + n_proj, pose_ned[1] + e_proj, pose_ned[2] + d_proj ]
            pt_list.append(p)
        return pt_list

    def polyval2d(self, x, y, m):
        order = int(np.sqrt(len(m))) - 1
        ij = itertools.product(range(order+1), range(order+1))
        z = np.zeros_like(x)
        for a, (i,j) in zip(m, ij):
            z += a * x**i * y**j
        return z

    def intersectVectorWithPoly(self, pose_ned, v, m):
        pass
    
    # given a set of vectors in the ned frame, and a starting point.
    # Find the intersection points with the given 2d polynomial.  For
    # any vectors which point into the sky, return just the original
    # reference/starting point.
    def intersectVectorsWithPoly(self, pose_ned, m, v_list):
        pt_list = []
        for v in v_list:
            p = self.intersectVectorWithPoly(pose_ned, m, v.flatten())
            pt_list.append(p)
        return pt_list

    # build an interpolation table for 'fast' projection of keypoints
    # into 3d world space
    #
    # 1. make a grid (i.e. 8x8) of uv coordinates covering the whole image
    # 2. undistort these uv coordinates
    # 3. project them into vectors
    # 4. intersect them with the srtm terrain to get ned coordinates
    # 5. use linearndinterpolator ... g = scipy.interpolate.LinearNDInterpolator([[0,0],[1,0],[0,1],[1,1]], [[0,4,8],[1,3,2],[2,2,-4],[4,1,0]])
    #    with origin uv vs. 3d location to build a table
    # 6. interpolate original uv coordinates to 3d locations
    def fastProjectKeypointsTo3d(self, sss):
        bar = Bar('Projecting keypoints to 3d:',
                  max = len(self.image_list))
        K = self.cam.get_K()
        IK = np.linalg.inv(K)
        for image in self.image_list:
            # build a regular grid of uv coordinates
            w, h = image.get_size()
            steps = 32
            u_grid = np.linspace(0, w-1, steps+1)
            v_grid = np.linspace(0, h-1, steps+1)
            uv_raw = []
            for u in u_grid:
                for v in v_grid:
                    uv_raw.append( [u,v] )
                    
            # undistort the grid of points
            uv_grid = self.undistort_uvlist(image, uv_raw)

            # filter crazy values when can happen out at the very fringes
            half_width = w * 0.5
            half_height = h * 0.5
            uv_filt = []
            for i, p in enumerate(uv_grid):
                if p[0] < -half_width or p[0] > w + half_width:
                    print("rejecting width outlier:", p, '(', uv_raw[i], ')')
                    continue
                if p[1] < -half_height or p[1] > h + half_height:
                    print("rejecting height outlier:", p, '(', uv_raw[i], ')')
                    continue
                uv_filt.append(p)
            print('raw pts:', len(uv_raw), 'undist pts:', len(uv_filt))
            
            # project the grid out into vectors
            body2ned = image.get_body2ned() # IR

            # M is a transform to map the lens coordinate system (at
            # zero roll/pitch/yaw to the ned coordinate system at zero
            # roll/pitch/yaw).  It is essentially a +90 pitch followed
            # by +90 roll (or equivalently a +90 yaw followed by +90
            # pitch.)
            cam2body = image.get_cam2body()
            
            vec_list = self.projectVectors(IK, body2ned, cam2body, uv_filt)

            # intersect the vectors with the surface to find the 3d points
            ned, ypr, quat = image.get_camera_pose()
            coord_list = sss.interpolate_vectors(ned, vec_list)

            # filter the coordinate list for bad interpolation
            coord_filt = []
            for i in reversed(range(len(coord_list))):
                if np.isnan(coord_list[i][0]):
                    print("rejecting ground interpolation fault:", uv_filt[i])
                    coord_list.pop(i)
                    uv_filt.pop(i)

            # build the multidimenstional interpolator that relates
            # undistored uv coordinates to their 3d location.  Note we
            # could also relate the original raw/distored points to
            # their 3d locations and interpolate from the raw uv's,
            # but we already have a convenient list of undistored uv
            # points.
            g = scipy.interpolate.LinearNDInterpolator(uv_filt, coord_list)

            # interpolate all the keypoints now to approximate their
            # 3d locations
            image.coord_list = []
            for i, uv in enumerate(image.uv_list):
                if image.kp_used[i]:
                    coord = g(uv)
                    # coord[0] is the 3 element vector
                    if not np.isnan(coord[0][0]):
                        image.coord_list.append(coord[0])
                    else:
                        print("nan alert!")
                        print("a feature is too close to an edge and undistorting puts it in a weird place.")
                        print("  uv:", uv, "coord:", coord)
                        print("  orig:", image.kp_list[i].pt)
                        #or append zeros which would be a hack until
                        #figuring out the root cause of the problem
                        #... if it isn't wrong image dimensions in the
                        #.info file...
                        #
                        image.coord_list.append(np.zeros(3)*np.nan)
                else:
                    image.coord_list.append(np.zeros(3)*np.nan)
            bar.next()
        bar.finish()
        
    def fastProjectKeypointsToGround(self, ground_m, cam_dict=None):
        bar = Bar('Projecting keypoints to 3d:',
                  max = len(self.image_list))
        for image in self.image_list:
            K = self.cam.get_K()
            IK = np.linalg.inv(K)
            
            # project the grid out into vectors
            if cam_dict == None:
                body2ned = image.get_body2ned() # IR
            else:
                body2ned = image.rvec_to_body2ned(cam_dict[image.name]['rvec'])
                
            # M is a transform to map the lens coordinate system (at
            # zero roll/pitch/yaw to the ned coordinate system at zero
            # roll/pitch/yaw).  It is essentially a +90 pitch followed
            # by +90 roll (or equivalently a +90 yaw followed by +90
            # pitch.)
            cam2body = image.get_cam2body()
            
            vec_list = self.projectVectors(IK, body2ned, cam2body, image.uv_list)

            # intersect the vectors with the surface to find the 3d points
            if cam_dict == None:
                pose = image.camera_pose
            else:
                pose = cam_dict[image.name]
            pts_ned = self.intersectVectorsWithGroundPlane(pose['ned'],
                                                           ground_m, vec_list)
            image.coord_list = pts_ned
            
            bar.next()
        bar.finish()
