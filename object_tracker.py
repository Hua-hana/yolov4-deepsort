import os
# comment out below line to enable tensorflow logging outputs
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
import time
import tensorflow as tf
physical_devices = tf.config.experimental.list_physical_devices('GPU')
if len(physical_devices) > 0:
    tf.config.experimental.set_memory_growth(physical_devices[0], True)
from absl import app, flags, logging
from absl.flags import FLAGS
import core.utils as utils
from core.yolov4 import filter_boxes
from tensorflow.python.saved_model import tag_constants
from core.config import cfg
from PIL import Image
import cv2
import numpy as np
import matplotlib.pyplot as plt
from tensorflow.compat.v1 import ConfigProto
from tensorflow.compat.v1 import InteractiveSession
# deep sort imports
from deep_sort import preprocessing, nn_matching
from deep_sort.detection import Detection
from deep_sort.tracker import Tracker
from tools import generate_detections as gdet
import perspective_transfrom as pt
from copy import copy,deepcopy
import collections

flags.DEFINE_string('framework', 'tf', '(tf, tflite, trt')
flags.DEFINE_string('weights', './checkpoints/yolov4-416',
                    'path to weights file')
flags.DEFINE_integer('size', 416, 'resize images to')
flags.DEFINE_boolean('tiny', False, 'yolo or yolo-tiny')
flags.DEFINE_string('model', 'yolov4', 'yolov3 or yolov4')
flags.DEFINE_string('video', './data/video/test.mp4', 'path to input video or set to 0 for webcam')
flags.DEFINE_string('output', None, 'path to output video')
flags.DEFINE_string('output_format', 'XVID', 'codec used in VideoWriter when saving video to file')
flags.DEFINE_float('iou', 0.45, 'iou threshold')
flags.DEFINE_float('score', 0.50, 'score threshold')
flags.DEFINE_boolean('dont_show', False, 'dont show video output')
flags.DEFINE_boolean('info', False, 'show detailed info of tracked objects')
flags.DEFINE_boolean('count', False, 'count objects being tracked on screen')
flags.DEFINE_string('birdview',None,'path to output bird-view video')
#get the perspective points
#ref_points=extract_pixel_pos.extract_points()
# video_points=[(1431, 397), (450, 397), (940, 97), (940, 1022)]
# soccer_field_points=[(594,327),(429,327),(512,26),(512,626)]
soccer_filed_img=cv2.imread('data/video/Soccer_field.png')


def getColorList():
    dict = collections.defaultdict(list)
 
    # 白色
    lower_white = np.array([0, 0, 221])
    upper_white = np.array([180, 30, 255])
    color_list = []
    color_list.append(lower_white)
    color_list.append(upper_white)
    color_list.append([248, 248, 255])
    dict['white'] = color_list

 
    #蓝色
    lower_blue = np.array([100, 30, 30])
    #upper_blue = np.array([124, 255, 255])
    upper_blue = np.array([150, 120, 120])
    color_list = []
    color_list.append(lower_blue)
    color_list.append(upper_blue)
    color_list.append([0, 0, 221])
    dict['blue'] = color_list
 
    #red
    lower_red = np.array([0, 150, 150])
    upper_red = np.array([10, 255, 255])
    color_list = []
    color_list.append(lower_red)
    color_list.append(upper_red)
    color_list.append([255, 0, 0])#RGB or BGR??
    dict['red'] = color_list

    return dict

def get_color(frame):
    hsv = cv2.cvtColor(frame,cv2.COLOR_BGR2HSV)
    maxsum = -100
    color = None
    color_dict = getColorList()
    for d in color_dict:
        mask = cv2.inRange(hsv,color_dict[d][0],color_dict[d][1])
        #binary = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)[1]
        #binary = cv2.dilate(binary,None,iterations=2)
        #cnts, hiera = cv2.findContours(binary.copy(),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        s = sum(mask.flatten())
        #for c in cnts:
        #    sum+=cv2.contourArea(c)
        if s > maxsum :
            maxsum = s
            color = d
    # if(color=='blue'):
    #     for d in color_dict:
    #         mask = cv2.inRange(hsv,color_dict[d][0],color_dict[d][1])
    #         print(sum(mask.flatten()))

    return color



def main(_argv):
    # Definition of the parameters
    max_cosine_distance = 0.4
    nn_budget = None
    nms_max_overlap = 1.0
    
    # initialize deep sort
    model_filename = 'model_data/mars-small128.pb'
    encoder = gdet.create_box_encoder(model_filename, batch_size=1)
    # calculate cosine distance metric
    metric = nn_matching.NearestNeighborDistanceMetric("cosine", max_cosine_distance, nn_budget)
    # initialize tracker
    tracker = Tracker(metric)

    # load configuration for object detector
    config = ConfigProto()
    config.gpu_options.allow_growth = True
    session = InteractiveSession(config=config)
    STRIDES, ANCHORS, NUM_CLASS, XYSCALE = utils.load_config(FLAGS)
    input_size = FLAGS.size
    video_path = FLAGS.video

    # load tflite model if flag is set
    if FLAGS.framework == 'tflite':
        interpreter = tf.lite.Interpreter(model_path=FLAGS.weights)
        interpreter.allocate_tensors()
        input_details = interpreter.get_input_details()
        output_details = interpreter.get_output_details()
        print(input_details)
        print(output_details)
    # otherwise load standard tensorflow saved model
    else:
        saved_model_loaded = tf.saved_model.load(FLAGS.weights, tags=[tag_constants.SERVING])
        infer = saved_model_loaded.signatures['serving_default']

    # begin video capture
    try:
        vid = cv2.VideoCapture(int(video_path))
    except:
        vid = cv2.VideoCapture(video_path)

    out = None
    soccer_filed_out=None

    # get video ready to save locally if flag is set
    if FLAGS.output:
        # by default VideoCapture returns float instead of int
        width = int(vid.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(vid.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = int(vid.get(cv2.CAP_PROP_FPS))
        codec = cv2.VideoWriter_fourcc(*FLAGS.output_format)
        out = cv2.VideoWriter(FLAGS.output, codec, fps, (width, height))

    if FLAGS.birdview:
        (height,width,_)=soccer_filed_img.shape #attention
        fps = int(vid.get(cv2.CAP_PROP_FPS))#use the input video fps
        codec = cv2.VideoWriter_fourcc(*FLAGS.output_format)
        soccer_filed_out=cv2.VideoWriter(FLAGS.birdview,codec,fps,(width,height))

    color_dict = getColorList()
    frame_num = 0
    # while video is running
    while True:
        return_value, frame = vid.read()
        if return_value:
            pic = frame.copy()
            #cv2.imwrite("pic.png", pic)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(frame)
        else:
            print('Video has ended or failed, try a different video format!')
            break
        frame_num +=1
        print('Frame #: ', frame_num)
        frame_size = frame.shape[:2]
        # why input_size default is 416
        image_data = cv2.resize(frame, (input_size, input_size))
        image_data = image_data / 255.
        image_data = image_data[np.newaxis, ...].astype(np.float32)
        start_time = time.time()

        # run detections on tflite if flag is set
        if FLAGS.framework == 'tflite':
            interpreter.set_tensor(input_details[0]['index'], image_data)
            interpreter.invoke()
            pred = [interpreter.get_tensor(output_details[i]['index']) for i in range(len(output_details))]
            # run detections using yolov3 if flag is set
            if FLAGS.model == 'yolov3' and FLAGS.tiny == True:
                boxes, pred_conf = filter_boxes(pred[1], pred[0], score_threshold=0.25,
                                                input_shape=tf.constant([input_size, input_size]))
            else:
                boxes, pred_conf = filter_boxes(pred[0], pred[1], score_threshold=0.25,
                                                input_shape=tf.constant([input_size, input_size]))
        else:
            batch_data = tf.constant(image_data)
            #infer input format and output format?
            pred_bbox = infer(batch_data)
            for key, value in pred_bbox.items():
                #n*m*k 取k的前4位（bbox的必要属性）n是batch_size?
                boxes = value[:, :, 0:4]
                pred_conf = value[:, :, 4:]
        #shape of boxes [batch_size, max_detections, 4]
        boxes, scores, classes, valid_detections = tf.image.combined_non_max_suppression(
            #tf.shape(boxes) = [n,m,4]   shape= [batch_size, num_boxes, q, 4]
            boxes=tf.reshape(boxes, (tf.shape(boxes)[0], -1, 1, 4)),
            scores=tf.reshape(
                #[batch_size, num_boxes, num_classes]
                pred_conf, (tf.shape(pred_conf)[0], -1, tf.shape(pred_conf)[-1])),
            max_output_size_per_class=50,
            max_total_size=50,
            #重叠的调整策略可以修改这里
            iou_threshold=FLAGS.iou,
            #基于分数的调整策略可以修改这里
            score_threshold=FLAGS.score
        )

        # convert data to numpy arrays and slice out unused elements
        #batch_size=1?
        num_objects = valid_detections.numpy()[0]
        bboxes = boxes.numpy()[0]
        bboxes = bboxes[0:int(num_objects)]
        scores = scores.numpy()[0]
        scores = scores[0:int(num_objects)]
        classes = classes.numpy()[0]
        classes = classes[0:int(num_objects)]

        # format bounding boxes from normalized ymin, xmin, ymax, xmax ---> xmin, ymin, width, height
        original_h, original_w, _ = frame.shape
        bboxes = utils.format_boxes(bboxes, original_h, original_w)

        # store all predictions in one parameter for simplicity when calling functions
        pred_bbox = [bboxes, scores, classes, num_objects]

        # read in all class names from config
        class_names = utils.read_class_names(cfg.YOLO.CLASSES)

        # by default allow all classes in .names file
        #allowed_classes = list(class_names.values())
        
        # custom allowed classes (uncomment line below to customize tracker for only people)
        allowed_classes = ['person', 'sports ball']

        # loop through objects and use class index to get class name, allow only classes in allowed_classes list
        names = []
        deleted_indx = []
        for i in range(num_objects):
            class_indx = int(classes[i])
            class_name = class_names[class_indx]
            if class_name not in allowed_classes:
                deleted_indx.append(i)
            else:
                names.append(class_name)
        names = np.array(names)
        count = len(names)
        if FLAGS.count:
            cv2.putText(frame, "Objects being tracked: {}".format(count), (5, 35), cv2.FONT_HERSHEY_COMPLEX_SMALL, 2, (0, 255, 0), 2)
            print("Objects being tracked: {}".format(count))
        # delete detections that are not in allowed_classes
        bboxes = np.delete(bboxes, deleted_indx, axis=0)
        scores = np.delete(scores, deleted_indx, axis=0)

        # encode yolo detections and feed to tracker
        features = encoder(frame, bboxes)
        detections = [Detection(bbox, score, class_name, feature) for bbox, score, class_name, feature in zip(bboxes, scores, names, features)]

        #initialize color map
        cmap = plt.get_cmap('tab20b')
        colors = [cmap(i)[:3] for i in np.linspace(0, 1, 20)]

        # run non-maxima supression
        boxs = np.array([d.tlwh for d in detections])
        scores = np.array([d.confidence for d in detections])
        classes = np.array([d.class_name for d in detections])
        indices = preprocessing.non_max_suppression(boxs, classes, nms_max_overlap, scores)
        detections = [detections[i] for i in indices]       

        # Call the tracker
        tracker.predict()
        tracker.update(detections)

        #deepcopy from img
        soccer_filed_img_copy=deepcopy(soccer_filed_img)

        # update tracks
        for track in tracker.tracks:
            if not track.is_confirmed() or track.time_since_update > 1:
                continue 
            bbox = track.to_tlbr()
            #class_name = track.get_class()
            class_name = str(track.track_id)
            
        # draw bbox on screen
            #color = colors[int(track.track_id) % len(colors)]
            #color = [i * 255 for i in color]
            if(len(frame)*0.5<= abs(int(bbox[1]-bbox[3]))):
                continue

            # cv2.imwrite("p"+str(track.track_id)+".png", pic[int(max(0,min(bbox[1],bbox[3]))):int(max(bbox[1],bbox[3])), int(max(min(bbox[0],bbox[2]),0)):int(max(bbox[2],bbox[0]))])
            color_name = get_color(pic[int(max(0,min(bbox[1],bbox[3]))):int(max(bbox[1],bbox[3])), int(max(min(bbox[0],bbox[2]),0)):int(max(bbox[2],bbox[0]))])
            color = []
            for i in color_dict[color_name][2]:
                color.append(int(i))
            # if color=='blue':
            #     print(track.track_id)
            
            cv2.rectangle(frame, (int(bbox[0]), int(bbox[1])), (int(bbox[2]), int(bbox[3])), color, 2)
            cv2.rectangle(frame, (int(bbox[0]), int(bbox[1]-5)), (int(bbox[0])+(len(class_name)+len(str(track.track_id)))*7, int(bbox[1])), color, -1)
            #cv2.putText(frame, class_name + "-" + str(track.track_id),(int(bbox[0]), int(bbox[1]-10)),0, 0.3, (255,255,255),2)
            cv2.putText(frame, class_name,(int(bbox[0]), int(bbox[1]-10)),0, 0.3, (255,255,255),2)
        
        # draw the bird view use the bbox color
            #color=(100,100,255)
            people_point=((bbox[0]+bbox[2])/2,bbox[3])
            people_point=pt.perspective_transform(people_point)
            cv2.circle(soccer_filed_img_copy,people_point,2,color,thickness=2)
        # if enable info flag then print details about each track
            if FLAGS.info:
                print("Tracker ID: {}, Class: {},  BBox Coords (xmin, ymin, xmax, ymax): {}".format(str(track.track_id), class_name, (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))))

        # calculate frames per second of running detections
        fps = 1.0 / (time.time() - start_time)
        print("FPS: %.2f" % fps)
        result = np.asarray(frame)
        result = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        
        #bird view result
        bird_result=np.array(soccer_filed_img_copy)
        bird_result=cv2.cvtColor(bird_result,cv2.COLOR_RGB2BGR)

        if not FLAGS.dont_show:
            cv2.imshow("Output Video", result)
        
        #show the bird view
        cv2.imshow('Bird view',bird_result)

        # if output flag is set, save video file
        if FLAGS.output:
            out.write(result)
        if FLAGS.birdview:
            soccer_filed_out.write(bird_result)

        if cv2.waitKey(1) & 0xFF == ord('q'): break
    cv2.destroyAllWindows()

if __name__ == '__main__':
    try:
        app.run(main)
    except SystemExit:
        pass
