import sys
import os
import argparse
import logging
from time import time
from typing import KeysView
import audio_metadata
from audio_metadata import UnsupportedFormat
import pandas as pd
import matplotlib.pyplot as plt
import json
import warnings
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

APP_NAME = "mapparser"
VERSION = 1
WORKING_DIR = r"C:\Users\AZM\Documents\Python\osumapparser"
LOG_FILE = f'{APP_NAME}v{VERSION}log.txt'

OSU_LOCATION = r"C:\Users\AZM\AppData\Local\osu!"
MIN_BREAK_LENGTH = 10000 #ms

'''
Timeline
v1 2021 08 18
Completed, parses all maps outputs csv with 
mapid, diffname, mode, objects, length, active len, obj density, real dens
v1.1 2021 11 22
replaced mutagen with audio-metadata to get mp3 length
'''


'''
error throwers
audio stuff:
    no audio file present
    weird audio_metadata error on 764602 sex whales by sotarks
map stuff:
    no hit objects/timing points: eg tristam moonlight by zaspar 1130174

'''
# TODO
# calculate bpm maybe

# parse map into a dict
def split_map(map):
    logging.debug("Splitting map")
    section = ""
    info = {
            "Events": [],
            "TimingPoints": [],
            "HitObjects": [],
            }
    for l in map:
        l = l.strip()
        if l == "":
            continue

        if l.startswith("["):
            section = l[1:-1]
            continue
        
        sections = ["General", "Metadata", "Difficulty"]
        if section in sections:
            try:
                key, value = l.split(':')
            except:
                continue
            try:
                info[key] = float(value)
            except:
                info[key] = value.strip()
            continue

        sections = ["Events", "TimingPoints", "HitObjects"]
        if section in sections:
            info[section] += [l.strip()]
    return info

def get_mp3_length(track):
    logging.debug(f"Checking mp3 at: {track}")
    # format = track.split(".")[-1]
    try:
        audio = audio_metadata.load(track)
        length = audio["streaminfo"]["duration"]
    except UnsupportedFormat as e:
        logging.warning(f"Unsupported format for {track}, setting to -1: {e}")
        return -1
    except FileNotFoundError as e:
        logging.warning(f"Audio file at {track} not found, setting to -1: {e}")
        return -1
    except Exception as e:
        logging.error(f"Unexpected exception when loading audio: {e}")
        return -1
    if length < 1:
        logging.warning(f"This audio at {track} is sub one second, setting to -1")
        return -1
    return length

def get_first_bpm(timingpoints):
    try:
        return 60 * 1000 / float(timingpoints[0].split(",")[1])
    except IndexError:
        logging.warning("Map has no TimingPoints, setting bpm to -1")
        return -1

def get_spacing_distribution(hit_objects):
    try:
        prev_timing = int(hit_objects[0].split(",")[2])
    except IndexError:
        logging.warning("Map has no HitOjects, setting dist to -1")
        return -1
    occurences = {}
    for obj in hit_objects[1:]:
        timing = int(obj.split(",")[2])
        spacing =  timing - prev_timing
        if spacing in occurences:
            occurences[spacing] += 1
        else:
            occurences[spacing] = 1
        prev_timing = timing
    return occurences

def plot_dist(dist, name, cutoff=0, normalize_bpm=1):
    timings = [k for k in dist if dist[k] > cutoff]
    timings.sort(reverse=True)
    #print(timings)
    # some timings are offset by 1 ms so lets merge those
    fixed_timings = []
    occurences = []
    for t in timings:
        p = [t-1, t, t+1]
        _f = False
        for _p in p:
            if _p in fixed_timings:
                occurences[-1] += dist[t]
                _f = True
        if _f: continue
        fixed_timings.append(t)
        occurences.append(dist[t])
    #print(fixed_timings)

    # timings are ms between objs convert to bpm
    bpm_timings = [round(1000/t * 60, 0) for t in fixed_timings]
    if normalize_bpm == 1:
        df = pd.DataFrame({'bpm':bpm_timings, 'occurences':occurences})
        ax = df.plot.bar(x='bpm', y='occurences', rot=45, title=name)
    else:
        n_bpm_timigs = [round(t/normalize_bpm, 1) for t in bpm_timings]
        df = pd.DataFrame({'bpm ratio':n_bpm_timigs, 'occurences':occurences})
        ax = df.plot.bar(x='bpm ratio', y='occurences', rot=45, title=name, legend=f"normalized bpm: {normalize_bpm}")
    
    ax.bar_label(ax.containers[0])
    # makes the bar values appear inside them
    
    plt.show()

# active time being map time discounting start, breaks and end
def calculate_active_time(objects):
    # FIXME doesnt count for spinners/sliders being longer than the break length
    active_time = 0
    section_start = 0
    timing = 0
    for obj in objects:
        prev_timing = timing
        timing = int(obj.split(",")[2])

        if timing - prev_timing > MIN_BREAK_LENGTH:
            logging.debug(f"Break detected at {prev_timing}")
            active_time += prev_timing - section_start
            section_start = timing
    active_time += timing - section_start
    if active_time < 1:
        logging.warning("Active time is less than 1, setting to -1")
        return -1
    return active_time

def check_map(map_path):
    diff_name = map_path.split('\\')[-1]
    logging.info("-Checking map {}".format(diff_name))
    with open(map_path, mode="r", encoding='utf-8') as m:
        f = [l.strip() for l in m]
    map_contents = split_map(f)
    
    if "AudioFilename" not in map_contents:
        logging.info("Skipping last map, fuck this")
        return 0

    useful_info = {}
    try:
        useful_info["beatmapid"] = map_contents["BeatmapID"]
    except KeyError:
        useful_info["beatmapid"] = -1
        logging.warning(f"Beatmap at {map_path} has no ID")
    useful_info["diff_name"] = diff_name.replace(",", ";")
    try:
        useful_info["mode"] = map_contents["Mode"]
    except KeyError:
        useful_info["mode"] = 0
        logging.warning(f"Beatmap at {map_path} has no Mode, assuming standard")

    useful_info["bpm"] = get_first_bpm(map_contents["TimingPoints"])
    useful_info["num_objects"] = len(map_contents["HitObjects"])
    useful_info["mp3_length"] = get_mp3_length("\\".join(map_path.split('\\')[:-1]) + "\\" + map_contents["AudioFilename"])
    useful_info["active_length"] = calculate_active_time(map_contents["HitObjects"])/1000
    useful_info["object_density"] = useful_info["num_objects"]/useful_info["mp3_length"]
    useful_info["real_density"] = useful_info["num_objects"]/useful_info["active_length"]
    _a = get_spacing_distribution(map_contents["HitObjects"])
    useful_info["spacing_distribution"] = json.dumps(_a).replace(",","|").replace('"',"").replace(" ","")
    
    # plot dist graph
    # plot_dist(get_spacing_distribution(
    #     map_contents["HitObjects"]),
    #     diff_name[:-4],
    #     cutoff=10,
    #     normalize_bpm=int(useful_info["bpm"])
    #     )

    return useful_info

def main(args):
    # filters audio_metadata warnings
    warnings.filterwarnings('ignore')
    
    with open(args.out, "w") as out:
        out.write("mapid, diffname, mode, bpm, objects, length, active len, obj density, real dens, obj spacing distribution (timing:occurences separated by |)\n")
    map_sets = os.listdir(OSU_LOCATION + r"\Songs")
    
    for map_set in tqdm(map_sets):
        map_set_path = OSU_LOCATION + r"\Songs\\" + map_set
        logging.info(f"Checking mapset: {map_set_path}")
        files = os.listdir(map_set_path)
        for f in files:
            if f.endswith(".osu"):
                map_info = check_map(map_set_path + "\\" + f)
                if map_info == 0: continue
                values = []
                for value in map_info.values():
                    values.append(str(value))
                with open(args.out, "a") as out:
                    out.write(",".join(values)+"\n")
    
    # map_path = OSU_LOCATION + r"\Songs\100049 Himeringo - Yotsuya-san ni Yoroshiku\Himeringo - Yotsuya-san ni Yoroshiku (RLC) [cheesiest's Light Insane].osu"
    # map_path = OSU_LOCATION + r"\Songs\372510 THE ORAL CIGARETTES - Kyouran Hey Kids!!\THE ORAL CIGARETTES - Kyouran Hey Kids!! (monstrata) [God of Speed].osu"
    # map_path = OSU_LOCATION + r"\Songs\292301 xi - Blue Zenith\xi - Blue Zenith (Asphyxia) [FOUR DIMENSIONS].osu"
    # map_path = OSU_LOCATION + r"\Songs\1204383 meganeko - Feral (osu! edit)\meganeko - Feral (osu! edit) (Respirte) [Nowa's Extra].osu"
    # map_path = OSU_LOCATION + r"\Songs\24313 Team Nekokan - Can't Defeat Airman\Team Nekokan - Can't Defeat Airman (Blue Dragon) [Holy Shit! It's Airman!!].osu"
    # map_path = OSU_LOCATION + r"\Songs\807850 THE ORAL CIGARETTES - Mou Ii kai\THE ORAL CIGARETTES - Mou ii Kai (Nevo) [Rain].osu"
    # map_path = OSU_LOCATION + r"\Songs\396221 Kurokotei - Galaxy Collapse\Kurokotei - Galaxy Collapse (Doomsday is Bad) [Galactic].osu"
    # map_info = check_map(map_path)
    # values = []
    # for value in map_info.values():
    #     values.append(str(value))
    # print(",".join(values))

    return

if __name__ == '__main__':
    t0 = time()
    os.chdir(WORKING_DIR)
    
    # parse input
    parser = argparse.ArgumentParser(description="Osu map parser")
    parser.add_argument("-log", type=str, default="INFO", help="set log level for console output, WARNING/INFO/DEBUG")
    parser.add_argument("-logfile", type=str, default="DEBUG", help="sets file logging level, 0/CRITICAL/ERROR/WARNING/INFO/DEBUG, set to 0 to disable")
    parser.add_argument("-out", type=str, default="out.csv", help="name of output file")
    
    args = parser.parse_args()

    # setting up logger to info on terminal and debug on file
    log_format=logging.Formatter(f'%(asctime)s {APP_NAME} v{VERSION} %(levelname)s:%(name)s:%(funcName)s %(message)s')
    
    if args.logfile != "0":
        file_handler = logging.FileHandler(filename=LOG_FILE, mode="a")
        file_handler.setLevel(getattr(logging, args.logfile.upper()))
        file_handler.setFormatter(log_format)
        logging.getLogger().addHandler(file_handler)
    
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(getattr(logging, args.log.upper()))
    logging.getLogger().addHandler(stream_handler)

    if args.logfile != "0":
        logging.getLogger().setLevel(getattr(logging, args.logfile.upper()))
    else:
        logging.getLogger().setLevel(getattr(logging, args.log.upper()))
    
    logging.info(f"Started with arguments: {sys.argv}")

    with logging_redirect_tqdm():
        main(args)
    
    logging.info(f"Exited. Took {round(time() - t0, 3)}s")
    