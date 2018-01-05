#!/usr/bin/python3

# Load the original match file and the fitted match file and find the
# features that moved the furthest and review them.

import argparse
import pickle
import cv2
import math
import numpy as np
import os

import sys
sys.path.append('../lib')
import Groups
import ProjectMgr

import match_culling as cull

parser = argparse.ArgumentParser(description='Keypoint projection.')
parser.add_argument('--project', required=True, help='project directory')
parser.add_argument('--stddev', type=float, default=3, help='how many stddevs above the mean for auto discarding features')
parser.add_argument('--strong', action='store_true', help='remove entire match chain, not just the worst offending element.')
parser.add_argument('--interactive', action='store_true', help='interactively review reprojection errors from worst to best and select for deletion or keep.')

args = parser.parse_args()

proj = ProjectMgr.ProjectMgr(args.project)
proj.load_image_info()
proj.load_features()
proj.undistort_keypoints()

print("Loading matches_grouped...")
matches_grouped = pickle.load( open( os.path.join(args.project, "matches_grouped"), "rb" ) )
print("Loading matches_sba...")
matches_sba = pickle.load( open( os.path.join(args.project, "matches_sba"), "rb" ) )

# load the group connections within the image set
groups = Groups.load(args.project)
print('Main group size:', len(groups[0]))

# find the difference between original and sba positions (this is
# helpful for big movers, but depends on accuracy of the original
# point location estimate and surface height.)
def compute_movers(matches_grouped, matches_sba):
    print("Computing movers...")

    result_list = []
    for i in range(len(matches_grouped)):
        p1 = np.array(matches_grouped[i][0])
        p2 = np.array(matches_sba[i][0])
        error = np.linalg.norm(p1 - p2)
        result_list.append( [error, i, 0] )

    # sort by error, worst is first
    result_list = sorted(result_list, key=lambda fields: fields[0],
                         reverse=True)
    return result_list

# find matches that are likely to be 'volatile' because they are
# paired from nearly colocated camera poses.
def compute_shakers(matches):
    result_list = []
    for i, match in enumerate(matches):
        # compute avg of camera locations
        sum = np.zeros(3)
        count = 0
        for p in match[1:]:
            if p[0] in groups[0]:
                img = proj.image_list[p[0]]
                ned, ypr, q = img.get_camera_pose_sba()
                sum += np.array(ned)
                count += 1
        if count == 0:
            # not part of the gropu
            continue
        avg = sum / float(count)
        print(i, 'avg cam:', avg)

        # distance from feature to the average of the camera locations
        x = np.linalg.norm(np.array(match[0]) - avg)
        
        # compute separation metric
        sum = 0.0
        count = 0
        for p in match[1:]:
            if p[0] in groups[0]:
                img = proj.image_list[p[0]]
                ned, ypr, q = img.get_camera_pose_sba()
                d = np.linalg.norm(avg - np.array(ned))
                sum += d
                count += 1
        # the average error which is sort of like a radius
        y = sum / float(count)
        angle = math.atan2(y, x)
        print('  angle:', angle*180.0/math.pi)
        result_list.append( [ angle, i, 0 ] )
        
    # smallest is worst (forward sort)
    result_list = sorted(result_list, key=lambda fields: fields[0])
    return result_list

def mark_outliers(error_list, trim_stddev):
    print("Marking outliers...")
    sum = 0.0
    count = len(error_list)

    # numerically it is better to sum up a list of floatting point
    # numbers from smallest to biggest (error_list is sorted from
    # biggest to smallest)
    for line in reversed(error_list):
        sum += line[0]
        
    # stats on error values
    print(" computing stats...")
    mre = sum / count
    stddev_sum = 0.0
    for line in error_list:
        error = line[0]
        stddev_sum += (mre-error)*(mre-error)
    stddev = math.sqrt(stddev_sum / count)
    print("mre = %.4f stddev = %.4f" % (mre, stddev))

    # mark match items to delete
    print(" marking outliers...")
    mark_count = 0
    for line in error_list:
        # print "line:", line
        if line[0] > mre + stddev * trim_stddev:
            cull.mark_outlier(matches_sba, line[1], line[2], line[0])
            mark_count += 1
            
    return mark_count

# delete marked matches
def delete_marked_matches(matches):
    print(" deleting marked items...")
    for i in reversed(range(len(matches))):
        match = matches[i]
        has_bad_elem = False
        for j in reversed(range(1, len(match))):
            p = match[j]
            if p == [-1, -1]:
                has_bad_elem = True
                match.pop(j)
        if args.strong and has_bad_elem:
            print("deleting entire match that contains a bad element")
            matches.pop(i)
        elif len(match) < 3:
            print("deleting match that is now in less than 2 images:", match)
            matches.pop(i)

#error_list = compute_movers(matches_grouped, matches_sba)
error_list = compute_shakers(matches_sba)

if args.interactive:
    mark_list = cull.show_outliers(error_list, matches_sba, proj.image_list)
else:
    # trim outliers by some # of standard deviations high
    # (for movers) mark_sum = mark_outliers(error_list, args.stddev)
    # construct a 'mark list' from the most colocated image pairs (note,
    # 3+ way matches are less likely to show up on this bad list.)
    mark_list = []
    for line in error_list:
        if line[0] < 0.07:      # radians
            # less than 6 meter distance from center point
            mark_list.append( [line[1], line[2]] )

# mark selection
cull.mark_using_list(mark_list, matches_grouped)
cull.mark_using_list(mark_list, matches_sba)
mark_sum = len(mark_list)

# after marking the bad matches, now count how many remaining features
# show up in each image
for i in proj.image_list:
    i.feature_count = 0
for i, match in enumerate(matches_sba):
    for j, p in enumerate(match[1:]):
        if p[1] != [-1, -1]:
            image = proj.image_list[ p[0] ]
            image.feature_count += 1

purge_weak_images = False
if purge_weak_images:
    # make a dict of all images with less than 25 feature matches
    weak_dict = {}
    for i, img in enumerate(proj.image_list):
        # print img.name, img.feature_count
        if img.feature_count > 0 and img.feature_count < 25:
            weak_dict[i] = True
    print('weak images:', weak_dict)

    # mark any features in the weak images list
    for i, match in enumerate(matches_orig):
        #print 'before:', match
        for j, p in enumerate(match[1:]):
            if p[0] in weak_dict:
                 match[j+1] = [-1, -1]
                 mark_sum += 1
    for i, match in enumerate(matches_sba):
        #print 'before:', match
        for j, p in enumerate(match[1:]):
            if p[0] in weak_dict:
                 match[j+1] = [-1, -1]
                 mark_sum += 0      # don't count these in the mark_sum
        #print 'after:', match

if mark_sum > 0:
    print('Outliers removed from match lists:', mark_sum)
    result=input('Save these changes? (y/n):')
    if result == 'y' or result == 'Y':
        delete_marked_matches(matches_grouped)
        delete_marked_matches(matches_sba)
        # write out the updated match dictionaries
        print("Writing grouped matches...")
        pickle.dump(matches_grouped, open(os.path.join(args.project, "matches_grouped"), "wb"))
        print("Writing optimized matches...")
        pickle.dump(matches_sba, open(os.path.join(args.project, "matches_sba"), "wb"))

