import csv
import copy
import numpy as np
import pickle
from matplotlib import pyplot as pl
import seaborn as sb
import pandas as pd
import os
import math
from toolz.itertoolz import partition
from para_hmm_final import ParaMarkovModel
import pomegranate
import bayeslite as bl
from iventure.utils_bql import query
from iventure.utils_bql import subsample_table_columns
from collections import deque
from master import fishxyz_to_unitvecs, sphericalbout_to_xyz, p_map_to_fish, RealFishControl

# Next steps: make sure bayesDB runs look like real bouts. see what regression does too. 


class PreyCap_Simulation:
    def __init__(self, fishmodel, paramodel, simlen, simulate_para, *para_input):
        self.fishmodel = fishmodel
        self.paramodel = paramodel
        self.sim_length = simlen
        self.para_states = []
        self.model_para_xyz = []
        if para_input == ():
            self.para_xyz = [fishmodel.real_hunt["Para XYZ"][0],
                             fishmodel.real_hunt["Para XYZ"][1],
                             fishmodel.real_hunt["Para XYZ"][2]]
        else:
            self.para_xyz = para_input
        self.para_spherical = []
        self.fish_xyz = [fishmodel.real_hunt["Initial Conditions"][0]]
        self.fish_bases = []
        self.fish_pitch = [fishmodel.real_hunt["Initial Conditions"][1]]
        self.fish_yaw = [fishmodel.real_hunt["Initial Conditions"][2]]
        self.simulate_para = simulate_para
        self.interbouts= self.fishmodel.interbouts
        self.bout_durations = self.fishmodel.bout_durations
        self.interpolate = True
        self.create_para_trajectory()
        
    def create_para_trajectory(self):

        def make_accel_vectors():
            spacing = int(np.load('spacing.npy'))
            vx = [p[-1] - p[0] for p in partition(spacing, self.para_xyz[0])]
            vy = [p[-1] - p[0] for p in partition(spacing, self.para_xyz[1])]
            vz = [p[-1] - p[0] for p in partition(spacing, self.para_xyz[2])]
            acc = np.diff([vx, vy, vz])
            accel_vectors = zip(acc[0], acc[1], acc[2])
            return accel_vectors
        
                        
        if self.simulate_para:
            p_start_position = np.array([
                self.para_xyz[0][0], self.para_xyz[1][0], self.para_xyz[2][0]])
            p_position_5 = np.array([
                self.para_xyz[0][5], self.para_xyz[1][5], self.para_xyz[2][5]])
            start_vector = p_position_5 - p_start_position
            para_initial_conditions = [p_start_position, start_vector]
            px, py, pz, states, vmax = self.paramodel.generate_para(
                para_initial_conditions[1],
                para_initial_conditions[0], self.sim_length)
            # resets para_xyz for generative model
            self.para_xyz = [np.array(px), np.array(py), np.array(pz)]
            self.para_states = states

        else:
            accelerations = make_accel_vectors()
            self.para_states = self.paramodel.model.predict(accelerations)
            
    def run_simulation(self):

        def project_para(p_coords, nback, proj_forward, curr_frame):
            def final_pos(pc):
                fp = (
                    ((pc[curr_frame] - pc[curr_frame-nback])
                     / float(nback)) * proj_forward) + pc[curr_frame]
                return fp
            p_xyz = [final_pos(p) for p in p_coords]                
            return p_xyz

        last_finite_pvarbs = {}
        endtime_last_bout = 0
        hunt_result = 0
        framecounter = 0
        future_frame = 5
        bout_counter = 0
        # spacing is an integer describing the sampling of vectors by the para. i.e.       # draws a new accel vector every 'spacing' frames
        spacing = np.load('spacing.npy')
        vel_history = 6
        # fish_basis is origin, x, y, z unit vecs
        p_az_hist = deque(maxlen=vel_history)
        p_alt_hist = deque(maxlen=vel_history)
        p_dist_hist = deque(maxlen=vel_history)
        if self.simulate_para:
            px = np.interp(
                np.linspace(0,
                            self.para_xyz[0].shape[0],
                            self.para_xyz[0].shape[0] * spacing),
                range(self.para_xyz[0].shape[0]),
                self.para_xyz[0])
            py = np.interp(
                np.linspace(0,
                            self.para_xyz[1].shape[0],
                            self.para_xyz[1].shape[0] * spacing),
                range(self.para_xyz[1].shape[0]),
                self.para_xyz[1])
            pz = np.interp(
                np.linspace(0,
                            self.para_xyz[2].shape[0],
                            self.para_xyz[2].shape[0] * spacing),
                range(self.para_xyz[2].shape[0]),
                self.para_xyz[2])
        else:
            px, py, pz = self.para_xyz

        while True:
            fish_basis = fishxyz_to_unitvecs(self.fish_xyz[-1],
                                             self.fish_yaw[-1],
                                             self.fish_pitch[-1])
            self.fish_bases.append(fish_basis[1])
            if framecounter == len(px):
                print("hunt epoch complete w/out strike")
                hunt_result = 2
                break

            # could add in a 4th condition where para has left field of view. simply abs(para_az) > pi 
            
            if self.fishmodel.linear_predict_para or self.fishmodel.boutbound_prediction:
                if self.fishmodel.boutbound_prediction:
                    # if framecounter not greater than future_frame, don't update model_para_xyz.
                    # PROBLEM IS FIRST BOUT HAPPENS BEFORE FUTURE FRAME. fut frame is 10, bout is at 5.
                    # i think the right answer is you change future_frame like below, but don't make the
                    # model updates based on the conditional. 
                    if framecounter >= future_frame:
                        future_frame = framecounter + self.fishmodel.predict_forward_frames
                    if not self.fishmodel.linear_predict_para:
                        try:
                            self.model_para_xyz = [px[future_frame], py[future_frame], pz[future_frame]]
                        except IndexError:
                            self.model_para_xyz = [px[-1], py[-1], pz[-1]]
                    else:
                        self.model_para_xyz = project_para([px, py, pz],
                                            5, self.fishmodel.predict_forward_frames, framecounter)
                            # print("MODEL XYZ BOTH")
                            # print self.model_para_xyz
                            # print framecounter
                else:
                    self.model_para_xyz = project_para([px, py, pz],
                                                       5, self.fishmodel.predict_forward_frames, framecounter)
                    # print("MODEL XYZ -- EXTRAP")
                    # print self.model_para_xyz
                    # print framecounter

            else:
                self.model_para_xyz = [px[framecounter], py[framecounter], pz[framecounter]]
            self.fishmodel.current_target_xyz = self.model_para_xyz
            para_spherical = p_map_to_fish(fish_basis[1],
                                           fish_basis[0],
                                           fish_basis[3],
                                           fish_basis[2],
                                           self.model_para_xyz, 
                                           0)
            # If project 10 frames forward with known XYZ, futureframe = framecoutner + 10
            # model_para_xyz = [px[futureframe], py[futureframe], pz[futureframe]]
            p_az_hist.append(para_spherical[0])
            p_alt_hist.append(para_spherical[1])
            p_dist_hist.append(para_spherical[2])
            para_varbs = {"Para Az": para_spherical[0],
                          "Para Alt": para_spherical[1],
                          "Para Dist": para_spherical[2],
                          "Para Az Velocity": np.mean(np.diff(p_az_hist)),
                          "Para Alt Velocity": np.mean(np.diff(p_alt_hist)),
                          "Para Dist Velocity": np.mean(np.diff(p_dist_hist))}
            print framecounter
            if framecounter == 2000:
                print("EVASIVE PARA!!!!!")
                self.para_xyz[0] = px
                self.para_xyz[1] = py
                self.para_xyz[2] = pz
                break
            
            if self.fishmodel.strike(
                    para_varbs) and framecounter-endtime_last_bout >= self.fishmodel.strike_refractory_period:
                print("Para Before Strike")
                print para_varbs
                print("STRIKE!!!!!!!!")
                nanstretch = np.full(
                    len(px)-framecounter, np.nan).tolist()
                print('para lengths')
                print len(nanstretch)
                self.para_xyz[0] = np.concatenate(
                    (px[0:framecounter], nanstretch), axis=0)
                self.para_xyz[1] = np.concatenate(
                    (py[0:framecounter], nanstretch), axis=0)
                self.para_xyz[2] = np.concatenate(
                    (pz[0:framecounter], nanstretch), axis=0)
                np.save('strikelist.npy', np.zeros(framecounter).tolist() + [1]
                        + np.zeros(len(px) - framecounter -1).tolist() )
                print self.para_xyz[0].shape
                hunt_result = 1
                # note that para may be "eaten" by model faster than in real life (i.e. it enters the strike zone)
                # 
                break

            ''' you will store these vals in a
            list so you can determine velocities and accelerations '''
            if framecounter in self.interbouts:
                self.fishmodel.current_fish_xyz = self.fish_xyz[-1]
                self.fishmodel.current_fish_pitch = self.fish_pitch[-1]
                self.fishmodel.current_fish_yaw = self.fish_yaw[-1]
                print para_varbs
                if not np.isfinite(self.model_para_xyz).all():
                    # here para record ends before fish catches.
                    # para can't be nan at beginning due to real fish filters in master.py
                    hunt_result = 3
                    break
                else:
                    last_finite_pvarbs = copy.deepcopy(para_varbs)
                fish_bout = self.fishmodel.model(para_varbs)
                dx, dy, dz = sphericalbout_to_xyz(fish_bout[0],
                                                  fish_bout[1],
                                                  fish_bout[2],
                                                  fish_basis[1],
                                                  fish_basis[3],
                                                  fish_basis[2])
                if self.interpolate:
                    x_prog = np.linspace(
                        self.fish_xyz[-1][0],
                        self.fish_xyz[-1][0] + dx,
                        self.bout_durations[bout_counter]).tolist()
                    y_prog = np.linspace(
                        self.fish_xyz[-1][1],
                        self.fish_xyz[-1][1] + dy,
                        self.bout_durations[bout_counter]).tolist()
                    z_prog = np.linspace(
                        self.fish_xyz[-1][2],
                        self.fish_xyz[-1][2] + dz,
                        self.bout_durations[bout_counter]).tolist()
                    yaw_prog = np.linspace(
                        self.fish_yaw[-1],
                        self.fish_yaw[-1] + fish_bout[4],
                        self.bout_durations[bout_counter]).tolist()
                    pitch_prog = np.linspace(
                        self.fish_pitch[-1],
                        self.fish_pitch[-1] + fish_bout[3],
                        self.bout_durations[bout_counter]).tolist()
                    self.fish_xyz += zip(x_prog, y_prog, z_prog)
                    self.fish_pitch += pitch_prog
                    self.fish_yaw += yaw_prog
                    for x, y, z, p, yw in zip(x_prog[1:],
                                              y_prog[1:],
                                              z_prog[1:], pitch_prog[1:], yaw_prog[1:]):
                        fish_basis = fishxyz_to_unitvecs((x,y,z), yw, p)
                        self.fish_bases.append(fish_basis[1])
                    framecounter += self.bout_durations[bout_counter]
                    endtime_last_bout = framecounter

                else:
                    new_xyz = self.fish_xyz[-1] + np.array([dx, dy, dz])
                    self.fish_xyz.append(new_xyz)
                    self.fish_pitch.append(self.fish_pitch[-1] + fish_bout[3])
                    self.fish_yaw.append(self.fish_yaw[-1] + fish_bout[4])
                    framecounter += 1
                bout_counter += 1
            else:
                self.fish_xyz.append(self.fish_xyz[-1])
                self.fish_pitch.append(self.fish_pitch[-1])
                self.fish_yaw.append(self.fish_yaw[-1])
                framecounter += 1
        if hunt_result == 3:
            para_varbs = last_finite_pvarbs
        return self.fishmodel.num_bouts_generated, para_varbs, hunt_result


# 3 is greedy1.
# 4 is greedy + n frames
# 5 is linear interpolation -- can also do this with regression 
# 6 is greedy1 with only huntbouts
# 7 is greedy + 10 frames with only huntbouts

    
class FishModel:
    def __init__(self, modchoice, strike_params, real_hunt_input):
#        self.bdb_file = bl.bayesdb_open('Bolton_HuntingBouts_Sim_inverted.bdb')
        self.modchoice = modchoice
        self.spherical_bouts = []
        self.bdb_file = bl.bayesdb_open('bdb_hunts_inverted.bdb')
        if modchoice == "Real":
            self.model = (lambda pv: self.real_fish(pv))
        elif modchoice == "Regression":
            self.model = (lambda pv: self.regression_model(pv))
        elif modchoice == "Bayes":
            self.model = (lambda pv: self.bdb_model(pv))
        elif modchoice == "Ideal":
            self.model = (lambda pv: self.ideal_model(pv))
        elif modchoice == "Random":
            self.model = (lambda pv: self.random_model(pv))
        self.linear_predict_para = False
        self.boutbound_prediction = False
        self.predict_forward_frames = 0
        self.strike_means = strike_params[0]
        self.strike_std = strike_params[1]
        self.strike_refractory_period = strike_params[2]
        self.real_hunt = real_hunt_input
        self.interbouts = real_hunt_input["Interbouts"]
        self.bout_durations = real_hunt_input["Bout Durations"]
        self.current_fish_xyz = []
        self.current_fish_yaw = 0
        self.current_fish_pitch = 0
        self.current_target_xyz = []
        self.interbouts = np.cumsum(
            self.interbouts) + real_hunt_input["First Bout Delay"]
        # note that the fish is given 5 frames before initializing a bout.
        # this is so the model has a proper velocity to work with. occurs
        # at the exact same time as the fish bouts in reality. para
        # trajectory is contwin + intwin - 5 to start. 
        self.num_bouts_generated = 0


    def generate_random_interbouts(self, num_bouts):
        all_ibs = np.floor(np.random.random(num_bouts) * 100)
        bout_indices = np.cumsum(all_ibs)
        return bout_indices

    def strike(self, p):
        in_az_win = (p["Para Az"] < self.strike_means[0] + self.strike_std[0] and
                     p["Para Az"] > self.strike_means[0] - self.strike_std[0])
        in_alt_win = (p["Para Alt"] < self.strike_means[1] + self.strike_std[1] and
                     p["Para Alt"] > self.strike_means[1] - self.strike_std[1])
        in_dist_win = (p["Para Dist"] < self.strike_means[2] + self.strike_std[2] and
                     p["Para Dist"] > self.strike_means[2] - self.strike_std[2])
        if in_az_win and in_alt_win and in_dist_win:
            return True
        else:
            return False

    def real_fish(self, para_varbs):
        hunt_df = self.real_hunt[
            "Hunt Dataframe"].loc[self.num_bouts_generated]
        bout = np.array(
            [hunt_df["Bout Az"],
             hunt_df["Bout Alt"],
             hunt_df["Bout Dist"],
             hunt_df["Bout Delta Pitch"],
             -1*hunt_df["Bout Delta Yaw"]])
        self.num_bouts_generated += 1
        return bout

    def random_model(self, para_varbs):
        self.num_bouts_generated += 1
        r_int = np.random.randint(0, len(self.spherical_bouts))
        rand_bout = self.spherical_bouts[r_int]
        bout = np.array(
            [rand_bout["Bout Az"],
             rand_bout["Bout Alt"],
             rand_bout["Bout Dist"],
             rand_bout["Delta Pitch"],
             -1*rand_bout["Delta Yaw"]])
        return bout

    def ideal_model(self, para_varbs):

        def score_para(p_results):
            # first ask if any para are IN strike zone
            strikes = np.array([self.strike(p) for p in p_results])
            if strikes.any():
                strike_args = np.argwhere(strikes)
                rand_strike = np.random.randint(strike_args.shape[0])
                bout_choice = strike_args[rand_strike][0]
                return bout_choice
            az = np.array([p['Para Az'] for p in p_results]) - self.strike_means[0]
            alt = np.array([p['Para Alt'] for p in p_results]) - self.strike_means[1]
            dist = np.array([p['Para Dist'] for p in p_results]) - self.strike_means[2]
            norm_az = az / np.std(az)
            norm_alt = alt / np.std(alt)
            norm_dist = dist / np.std(dist)
            score = np.abs(norm_az) + np.abs(norm_alt) + np.abs(norm_dist)
            bout_choice = np.argmin(score)
            return bout_choice
 
        # Use this model for all greedy models and simply manipulate the model_para_xyz
        self.num_bouts_generated += 1
        fish_basis = fishxyz_to_unitvecs(self.current_fish_xyz,
                                         self.current_fish_yaw,
                                         self.current_fish_pitch)
        para_results = []
        for bt in self.spherical_bouts:
            dx, dy, dz = sphericalbout_to_xyz(bt["Bout Az"],
                                              bt["Bout Alt"],
                                              bt["Bout Dist"],
                                              fish_basis[1],
                                              fish_basis[3],
                                              fish_basis[2])
            temp_yaw = -1*bt["Delta Yaw"] + self.current_fish_yaw
            temp_pitch = bt["Delta Pitch"] + self.current_fish_pitch
            temp_xyz = np.array(self.current_fish_xyz) + np.array([dx, dy, dz])
            temp_fish_basis = fishxyz_to_unitvecs(temp_xyz, 
                                                  temp_yaw, 
                                                  temp_pitch)
            postbout_para_spherical = p_map_to_fish(temp_fish_basis[1],
                                                    temp_fish_basis[0],
                                                    temp_fish_basis[3],
                                                    temp_fish_basis[2],
                                                    self.current_target_xyz,
                                                    0)
            pvarbs = {"Para Az": postbout_para_spherical[0],
                      "Para Alt": postbout_para_spherical[1],
                      "Para Dist": postbout_para_spherical[2]}
            para_results.append(pvarbs)
        best_bout = self.spherical_bouts[score_para(para_results)]

        bout_array = np.array([best_bout['Bout Az'],
                               best_bout['Bout Alt'],
                               best_bout['Bout Dist'], best_bout['Delta Pitch'], -1*best_bout['Delta Yaw']])
        return bout_array
    
    def regression_model(self, para_varbs):
        self.num_bouts_generated += 1
        bout_az = (para_varbs['Para Az'] * 1.36) + .02
        # NOTE THAT YOUR BOUT DESCRIPTOR IN MASTER.PY NOW OUTPUTS THE RIGHT SIGN OF DELTA YAW. CAN GET RID OF THISINVERSION WHEN ANALYZING NEW DATASET 
        bout_yaw = -1 * ((.46 * para_varbs['Para Az']) - .02)
        bout_alt = (1.5 * para_varbs['Para Alt']) + -.37
        bout_pitch = (.27 * para_varbs['Para Alt']) - .04
        bout_dist = (.09 * para_varbs['Para Dist']) + 29
        bout_array = np.array([bout_az,
                               bout_alt,
                               bout_dist, bout_pitch, bout_yaw])
        noise_array = np.ones(5)
        # noise_array = np.array(
        # [(np.random.random() * .4) + .8 for i in bout_array])
        bout = bout_array * noise_array
        return bout

    def bdb_model(self, para_varbs):
        self.num_bouts_generated += 1
        invert = True
#        invert = False
        sampling = 'median'
#        sampling = 'sample'
        
        def invert_pvarbs(pvarbs):
            pvcopy = copy.deepcopy(pvarbs)
            invert_az = False
            invert_alt = False
            if pvcopy["Para Az"] < 0:
                pvcopy["Para Az"] *= -1
                pvcopy["Para Az Velocity"] *= -1
                invert_az = True
            if pvcopy["Para Alt"] < 0:
                pvcopy["Para Alt"] *= -1
                pvcopy["Para Alt Velocity"] *= -1
                invert_alt = True
            return pvcopy, invert_az, invert_alt

        def invert_bout(bout_dic, invert_az, invert_alt):
            bdic_copy = copy.deepcopy(bout_dic)
            if invert_az:
                bdic_copy["Bout Az"] *= -1
                bdic_copy["Bout Delta Yaw"] *= -1
            if invert_alt:
                bdic_copy["Bout Alt"] *= -1
                bdic_copy["Bout Delta Pitch"] *= -1
            return bdic_copy

        if invert:
            para_varbs, inv_az, inv_alt = invert_pvarbs(para_varbs)

        if sampling == 'median':
            df_sim = query(self.bdb_file,
                           ''' SIMULATE "Bout Az", "Bout Alt",
                           "Bout Dist", "Bout Delta Pitch", "Bout Delta Yaw"
                           FROM bout_population
                           GIVEN "Para Az" = {Para Az},
                           "Para Alt" = {Para Alt},
                           "Para Dist" = {Para Dist},
                           "Para Az Velocity" = {Para Az Velocity},
                           "Para Alt Velocity" = {Para Alt Velocity},
                           "Para Dist velocity" = {Para Dist Velocity}
--                           USING MODEL 37
                           LIMIT 500 '''.format(**para_varbs))
            bout_az = df_sim['Bout Az'].median()
            bout_alt = df_sim['Bout Alt'].median()
            bout_dist = df_sim['Bout Dist'].median()
            bout_pitch = df_sim['Bout Delta Pitch'].median()
            bout_yaw = -1*df_sim['Bout Delta Yaw'].median()

        elif sampling == 'sample':
            df_sim = query(self.bdb_file,
                           ''' SIMULATE "Bout Az", "Bout Alt",
                           "Bout Dist", "Bout Delta Pitch", "Bout Delta Yaw"
                           FROM bout_population
                           GIVEN "Para Az" = {Para Az},
                           "Para Alt" = {Para Alt},
                           "Para Dist" = {Para Dist},
                           "Para Az Velocity" = {Para Az Velocity},
                           "Para Alt Velocity" = {Para Alt Velocity},
                           "Para Dist velocity" = {Para Dist Velocity}
  --                         USING MODEL 37
                           LIMIT 1 '''.format(**para_varbs))
            bout_az = df_sim['Bout Az'][0]
            bout_alt = df_sim['Bout Alt'][0]
            bout_dist = df_sim['Bout Dist'][0]
            bout_pitch = df_sim['Bout Delta Pitch'][0]
            bout_yaw = -1*df_sim['Bout Delta Yaw'][0]

        b_dict = {"Bout Az": bout_az,
                  "Bout Alt": bout_alt,
                  "Bout Dist": bout_dist,
                  "Bout Delta Yaw": bout_yaw,
                  "Bout Delta Pitch": bout_pitch}
        if invert:
            b_dict = invert_bout(b_dict, inv_az, inv_alt)
        bout = np.array([b_dict["Bout Az"],
                         b_dict["Bout Alt"],
                         b_dict["Bout Dist"],
                         b_dict["Bout Delta Pitch"],
                         b_dict["Bout Delta Yaw"]])
        return bout


def mapped_para_generator(hb_data):
    ind = np.int(np.random.random() * len(hb_data['Para Az']))
    p_varbs = hb_data.iloc[ind][6:14]
    if len([a for a in p_varbs.values if math.isnan(a)]) != 0:
        return mapped_para_generator(hb_data)
    return p_varbs


def generate_bouts_from_csv(data_fr):
    num_samples = 2000
    file_id = os.getcwd() + '/regmodel'
    header = [a for a in data_fr.keys()[1:14]]
    with open(file_id + '.csv', 'wb') as csvfile:
        output_data = csv.writer(csvfile)
        for i in range(num_samples):
            para_vrbs = mapped_para_generator(data_fr)
            bout = boutgen_from_regression(para_vrbs)
            if i == 0:
                output_data.writerow(header)
            output_data.writerow(bout)

#use this to characterize how similar model bouts are to real choices


def characterize_strikes(hb_data):
    strike_characteristics = []
    for i in range(len(hb_data["Bout Number"])):
        if hb_data["Bout Number"][i] == -1:
            if hb_data["Strike Or Abort"][i] < 3:
                strike = np.array([hb_data["Para Az"][i],
                                   hb_data["Para Alt"][i],
                                   hb_data["Para Dist"][i]])
                if np.isfinite(strike).all():
                    strike_characteristics.append(strike)
    avg_strike_position = np.mean(strike_characteristics, axis=0)
    std = np.std(strike_characteristics, axis=0)
    return [avg_strike_position, std]


def view_and_sim_single_hunt(rfo, strike_params, para_model, model_params, hunt_id):
    realhunt_allframes(rfo, hunt_id)
    sim = model_wrapper(rfo, strike_params, para_model, model_params, hunt_id)    
    np.save('para_simulation.npy', sim.para_xyz)
    np.save('origin_model.npy', sim.fish_xyz[:-1])
    np.save('uf_model.npy', sim.fish_bases[:-1])
    return sim
    
def realhunt_allframes(rfo, hunt_id):
    delay = rfo.firstbout_para_intwin
    h_ind = np.argwhere(np.array(rfo.hunt_ids) == hunt_id)[0][0]
    frames = np.array(rfo.hunt_frames[h_ind]) - delay
    mod_input = rfo.model_input(h_ind)
    para_xyz = [mod_input["Para XYZ"][0],
                mod_input["Para XYZ"][1],
                mod_input["Para XYZ"][2]]
    np.save('ufish.npy', rfo.fish_orientation[frames[0]:frames[1]])
    np.save('ufish_origin.npy', rfo.fish_xyz[frames[0]:frames[1]])
    np.save('3D_paracoords.npy', para_xyz)

def model_wrapper(rfo, strike_params, para_model, model_params, *hunt_id):
    print("Executing Models")
    bout_container = [[] for i in range(len(model_params))]
    sequence_length = 10000
    p_xyz = []
    # this will iterate through hunt_ids and through types of Fishmodel.
    # function takes a real_fish_object and creates a Simulation for each hunt id and each model.
    # have to have the Sim output a -1 or something if the fish never catches the para
    if hunt_id == ():
        hunt_ids = rfo.hunt_ids
    else:
        hunt_ids = hunt_id
        
    for h_ind, hid in enumerate(hunt_ids):
        if hunt_id != ():
            h_ind = np.argwhere(np.array(rfo.hunt_ids) == hunt_id[0])[0][0]
        sim_created = False
        for mi, model_run in enumerate(model_params):
            print("Running " + model_run["Model Type"] + " on hunt " + str(hid))
            fish = FishModel(model_run["Model Type"],
                             strike_params, rfo.model_input(h_ind))
            try:
                if model_run["Extrapolate Para"]:
                    fish.linear_predict_para = True
                    fish.predict_forward_frames = model_run["Extrapolate Para"]
            except KeyError: pass
            try: 
                if model_run["Willie Mays"]:
                    fish.boutbound_prediction = True
                    fish.predict_forward_frames = model_run["Willie Mays"]
            except KeyError: pass
            if model_run["Model Type"] == "Ideal" or model_run["Model Type"] == "Random":
                fish.spherical_bouts = model_run["Spherical Bouts"]
            if model_run["Real or Sim"] == "Real":
                sim = PreyCap_Simulation(
                    fish,
                    para_model,
                    sequence_length,
                    False)
            elif model_run["Real or Sim"] == "Sim":
                # this allows same para sim to be run on all models for each hunt
                # if you want to run multiple sims per hunt, just submit the same dictionary
                # list with no real hunts in it.           
                if not sim_created:
                    sim = PreyCap_Simulation(
                        fish,
                        para_model,
                        sequence_length,
                        True)
                    p_xyz = sim.para_xyz
                    sim_created = True
                else: 
                    sim = PreyCap_Simulation(
                        fish,
                        para_model,
                        sequence_length,
                        False,
                        p_xyz)                                          
            bouts_and_p_params = sim.run_simulation()
            bout_container[mi].append(bouts_and_p_params)
# Note that bout container contains 3 elements. The number of bouts
# executed by the model, the coordinates of the para before the last bout
# and the result of the hunt.
    if hunt_id != ():
        return sim
    else:
        return bout_container
    

    # this will take the paramodel object
    

if __name__ == "__main__":
    csv_file = 'huntbouts_rad.csv'
    hb = pd.read_csv(csv_file)
    fish_id = '042318_6'
    real_fish_object = pd.read_pickle(
        os.getcwd() + '/' + fish_id + '/RealHuntData_' + fish_id + '.pkl')
    para_model = pickle.load(open(os.getcwd() + '/pmm.pkl', 'rb'))
    np.random.seed()
    sequence_length = 10000
    strike_params = characterize_strikes(hb)
    str_refractory_list = [b["Interbouts"][-1] - b["Bout Durations"][-2]
                           for b in [real_fish_object.model_input(i)
                                     for i in range(len(real_fish_object.hunt_ids))]]
    str_refractory = np.ceil(np.percentile(str_refractory_list, 10)).astype(np.int)
    strike_params.append(str_refractory)
    spherical_bouts = np.load('spherical_bouts.npy').tolist()
    spherical_huntbouts = np.load('spherical_huntbouts.npy').tolist()
    spherical_nonhunt_bouts = [b for b in spherical_bouts if b not in spherical_huntbouts]

    # Dict fields are  "Model Type" : Real, Regression, Bayes, Ideal, Random. This is the fish model
    #                  "Real or Sim": Real, Sim, which generates a para or uses real para coords
    #                  "Willie Mays": Enter number of frames ahead to predict
    #                  "Extrapolate Para": ''
    #                  "Spherical Bouts": Enter the list of possible bouts here
    #
    # Hunt Results are: 1 = Success, 2 = Fail, 3 = Para Rec nans before end of hunt
    
    modlist = [{"Model Type": "Regression", "Real or Sim": "Real"},
               {"Model Type": "Regression", "Real or Sim": "Real", "Extrapolate Para": 10},
               {"Model Type": "Ideal", "Real or Sim": "Real", "Spherical Bouts": spherical_bouts},
               {"Model Type": "Regression", "Real or Sim": "Real", "Willie Mays": 10},
               {"Model Type": "Regression", "Real or Sim": "Real", "Extrapolate Para": 10, "Willie Mays": 10},
               {"Model Type": "Random", "Real or Sim": "Real", "Spherical Bouts": spherical_bouts},
               {"Model Type": "Regression", "Real or Sim": "Sim"}]


#    bpm = model_wrapper(real_fish_object, strike_params, para_model, modlist)

    sim = view_and_sim_single_hunt(real_fish_object, strike_params, para_model, [modlist[-1]], 9)
    
# Problem with Sim is that the bouts run out of bouts. All bouts are completed, but fish stops moving after bouts. 

