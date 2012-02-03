import logging
import numpy as np

from analysis_engine.node import A, DerivedParameterNode, KTI, P, S

from analysis_engine.library import (bearings_and_distances,
                                     blend_alternate_sensors,
                                     blend_two_parameters,
                                     first_order_lag,
                                     first_order_washout,
                                     hysteresis,
                                     index_at_value,
                                     index_of_datetime,
                                     integrate,
                                     interleave,
                                     is_slice_within_slice,
                                     latitudes_and_longitudes,
                                     merge_sources,
                                     rate_of_change, 
                                     repair_mask,
                                     rms_noise,
                                     smooth_track,
                                     straighten_headings,
                                     vstack_params)

from settings import (AZ_WASHOUT_TC,
                      AT_WASHOUT_TC,
                      GROUNDSPEED_LAG_TC,
                      HYSTERESIS_FPALT,
                      HYSTERESIS_FPALT_CCD,
                      HYSTERESIS_FPIAS,
                      HYSTERESIS_FP_RAD_ALT,
                      HYSTERESIS_FPROC,
                      GRAVITY_IMPERIAL,
                      GRAVITY_METRIC,
                      KTS_TO_FPS,
                      KTS_TO_MPS,
                      RATE_OF_CLIMB_LAG_TC
                      )

#-------------------------------------------------------------------------------
# Derived Parameters


# Q: What do we do about accessing KTIs - params['a kti class name'] is a list of kti's
#   - could have a helper - filter_for('kti_name', take_max=True) # and possibly take_first, take_min, take_last??

# Q: Accessing information like ORIGIN / DESTINATION

# Q: What about V2 Vref etc?


class AccelerationVertical(DerivedParameterNode):
    def derive(self, acc_norm=P('Acceleration Normal'), 
               acc_lat=P('Acceleration Lateral'), 
               acc_long=P('Acceleration Longitudinal'), 
               pitch=P('Pitch'), roll=P('Roll')):
        """
        Resolution of three accelerations to compute the vertical
        acceleration (perpendicular to the earth surface). Result is in g,
        retaining the 1.0 datum and positive upwards.
        """
        # Simple Numpy algorithm working on masked arrays
        pitch_rad = np.radians(pitch.array)
        roll_rad = np.radians(roll.array)
        resolved_in_roll = acc_norm.array*np.ma.cos(roll_rad)\
            - acc_lat.array * np.ma.sin(roll_rad)
        self.array = resolved_in_roll * np.ma.cos(pitch_rad) \
                     + acc_long.array * np.ma.sin(pitch_rad)
        

class AccelerationForwards(DerivedParameterNode):
    def derive(self, acc_norm=P('Acceleration Normal'), 
               acc_long=P('Acceleration Longitudinal'), 
               pitch=P('Pitch')):
        """
        Resolution of three body axis accelerations to compute the forward
        acceleration, that is, in the direction of the aircraft centreline
        when projected onto the earth's surface. Forwards = +ve
        """
        # Simple Numpy algorithm working on masked arrays
        pitch_rad = np.radians(pitch.array)
        self.array = acc_long.array * np.cos(pitch_rad)\
                     - acc_norm.array * np.sin(pitch_rad)


class AccelerationAcrossTrack(DerivedParameterNode):
    def derive(self, acc_fwd=P('Acceleration Forwards'),
               acc_side=P('Acceleration Sideways'),
               drift=P('Drift')):
        """
        The forward and sideways ground-referenced accelerations are resolved
        into along track and across track coordinates in preparation for
        groundspeed computations.
        """
        drift_rad = np.radians(drift.array)
        self.array = acc_side.array * np.cos(drift_rad)\
            - acc_fwd.array * np.sin(drift_rad)


##class     name = 'Airspeed Minus V2 400 To 1500 Ft Min'
class AccelerationAlongTrack(DerivedParameterNode):
    def derive(self, acc_fwd=P('Acceleration Forwards'), 
               acc_side=P('Acceleration Sideways'), 
               drift=P('Drift')):
        """
        The forward and sideways ground-referenced accelerations are resolved
        into along track and across track coordinates in preparation for
        groundspeed computations.
        """
        drift_rad = np.radians(drift.array)
        self.array = acc_fwd.array * np.cos(drift_rad)\
                     + acc_side.array * np.sin(drift_rad)


class AccelerationSideways(DerivedParameterNode):
    def derive(self, acc_norm=P('Acceleration Normal'), 
               acc_lat=P('Acceleration Lateral'),
               acc_long=P('Acceleration Longitudinal'), 
               pitch=P('Pitch'), roll=P('Roll')):
        """
        Resolution of three body axis accelerations to compute the lateral
        acceleration, that is, in the direction perpendicular to the aircraft centreline
        when projected onto the earth's surface. Right = +ve.
        """
        pitch_rad = np.radians(pitch.array)
        roll_rad = np.radians(roll.array)
        # Simple Numpy algorithm working on masked arrays
        resolved_in_pitch = acc_long.array * np.sin(pitch_rad) \
                            + acc_norm.array * np.cos(pitch_rad)
        self.array = resolved_in_pitch * np.sin(roll_rad) \
                     + acc_lat.array * np.cos(roll_rad)

"""
-------------------------------------------------------------------------------
Superceded by Truck and Trailer analysis of airspeed during takeoff and landing
-------------------------------------------------------------------------------
class AccelerationForwardsForFlightPhases(DerivedParameterNode):
    # List the minimum acceptable parameters here
    @classmethod
    def can_operate(cls, available):
        if 'Airspeed' in available:
            return True
        elif 'Acceleration Longitudinal' in available:
            return True
        else:
            return False
        
    # List the optimal parameter set here
    def derive(self, acc_long=P('Acceleration Longitudinal'),
               airspeed=P('Airspeed')):
        '''
        Acceleration or deceleration on the runway is used to identify the
        runway heading. For the Hercules aircraft there is no longitudinal
        accelerometer, so rate of change of airspeed is used instead.
        '''
        if not acc_long: #  TODO: remove this inversion. Herc testing only.
            self.array = repair_mask(acc_long.array)
        else:
            '''
            This calculation is included for the few aircraft that do not
            have a longitudinal accelerometer installed, so we can identify
            acceleration or deceleration on the runway.
            '''
            # TODO: Remove float from line below
            aspd = P('Aspd',array=repair_mask(np.ma.array(airspeed.array.data, dtype='float')),frequency=airspeed.frequency)
            # Tacky smoothing to see how it works. TODO fix !
            roc_aspd = rate_of_change(aspd,1.5) * KTS_TO_FPS/GRAVITY
            self.array =  roc_aspd 
-------------------------------------------------------------------------------
Superceded by Truck and Trailer analysis of airspeed during takeoff and landing
-------------------------------------------------------------------------------
"""
            

class AirspeedForFlightPhases(DerivedParameterNode):
    def derive(self, airspeed=P('Airspeed')):
        self.array = hysteresis(airspeed.array, HYSTERESIS_FPIAS)


class AirspeedMinusV2(DerivedParameterNode):
    #TODO: TESTS
    def derive(self, airspeed=P('Airspeed'), v2=P('V2')):
        self.array = airspeed.array - v2.array

class AirspeedMinusVref(DerivedParameterNode):
    #TODO: TESTS
    def derive(self, airspeed=P('Airspeed'), vref=P('Vref')):
        self.array = airspeed.array - vref.array


class AirspeedTrue(DerivedParameterNode):
    #dependencies = ['SAT', 'VMO', 'MMO', 'Indicated Airspeed', 'Altitude QNH']
    # TODO: Move required dependencies from old format above to derive kwargs.
    def derive(self, ias = P('Airspeed'),
               alt_std = P('Altitude STD'),
               sat = P('SAT')):
        return NotImplemented
    

class AltitudeAAL(DerivedParameterNode):
    """
    This is the main altitude measure used during analysis. Where radio
    altimeter data is available, this is used for altitudes up to 100ft and
    thereafter the pressure altitude signal is used. The two are "joined"
    together at the sample above 100ft in the climb or descent as
    appropriate. Once joined, the altitude signal is inertially smoothed to
    provide accurate short term signals at the sample rate of the Rate of
    Climb parameter, and this also reduces any "join" effect at the signal
    transition.
    
    If no radio altitude signal is available, the simple measure
    "Altitude AAL For Flight Phases" is used instead, which provides perfecly
    workable solutions except that it tends to dip below the runway at
    takeoff and landing.
    """    
    name = "Altitude AAL"
    units = 'ft'

    @classmethod
    def can_operate(cls, available):
        return 'Altitude AAL For Flight Phases' in available
    
    def derive(self, roc = P('Rate Of Climb'),
               alt_aal_4fp = P('Altitude AAL For Flight Phases'),
               alt_std = P('Altitude STD'),
               alt_rad = P('Altitude Radio'),
               fast = S('Fast')):
        if alt_rad:
            # Initialise the array to zero, so that the altitude above the airfield
            # will be 0ft when the aircraft cannot be airborne.
            alt_aal = np.ma.masked_all_like(alt_std.array)
            
            for speedy in fast:
                # Populate the array with Altitude Radio data from one runway
                # to the next.
                alt_aal[speedy.slice] = np.ma.copy(alt_rad.array[speedy.slice])

                # Now look where the aircraft passed through 100ft Alt_Rad
                highs = np.ma.clump_unmasked(np.ma.masked_less
                                             (alt_rad.array[speedy.slice],100))
                
                # For each segment like this (allowing for touch and go's)
                for high in highs:
                    
                    # TODO: Allow for touch and go's (i.e. end indexes should refer to possible touch and gos.
                    
                    # Find the highest point, where we'll put the "step"
                    peak = np.ma.argmax(alt_std.array[speedy.slice][high])

                    # We want to make the climb data join just above 100ft
                    dh_climb = alt_rad.array[speedy.slice][high][0] - \
                        alt_std.array[speedy.slice][high][0]

                    # Shift the pressure altitude data with this adjustment
                    alt_aal.data[speedy.slice][high][0:peak] = \
                        alt_std.array.data[speedy.slice][high][0:peak] + dh_climb
                    alt_aal.mask[speedy.slice][high][0:peak] = \
                        np.ma.getmaskarray(alt_std.array)[speedy.slice][high][0:peak]

                    # ...and do the same on the way down...
                    dh_descend = alt_rad.array[speedy.slice][high][-1] - \
                        alt_std.array[speedy.slice][high][-1]

                    alt_aal.data[speedy.slice][high][peak:] = \
                        alt_std.array[speedy.slice][high][peak:] + dh_descend
                    alt_aal.mask[speedy.slice][high][peak:] = \
                        np.ma.getmaskarray(alt_std.array)[speedy.slice][high][peak:]
                
                # Use the complementary smoothing approach
                alt_aal_lag = first_order_lag(alt_aal[speedy.slice],
                                              RATE_OF_CLIMB_LAG_TC, roc.hz)
                
                roc_lag = first_order_lag(roc.array[speedy.slice],RATE_OF_CLIMB_LAG_TC, roc.hz,
                                          gain=RATE_OF_CLIMB_LAG_TC/60.0)
    
                alt_aal[speedy.slice] = (alt_aal_lag + roc_lag)
                
            self.array = alt_aal
            
        else:
            self.array = np.ma.copy(alt_aal_4fp.array) 

    
class AltitudeAALForFlightPhases(DerivedParameterNode):
    name = 'Altitude AAL For Flight Phases'
    units = 'ft'
    # This crude parameter is used for flight phase determination,
    # and only uses airspeed and pressure altitude for robustness.
    def derive(self, alt_std=P('Altitude STD'), fast=S('Fast')):
        
        # Initialise the array to zero, so that the altitude above the airfield
        # will be 0ft when the aircraft cannot be airborne.
        self.array = np.ma.masked_all_like(alt_std.array)
        
        altitude = repair_mask(alt_std.array) # Remove small sections of corrupt data
        for speedy in fast:
            begin = max(0, speedy.slice.start - 1)
            end = min(speedy.slice.stop, len(altitude)-1)
            peak = np.ma.argmax(altitude[speedy.slice])
            top = begin+peak+1
            # We override any negative altitude variations that occur at
            # takeoff or landing rotations. This parameter is only used for
            # flight phase determination so it is important that it behaves
            # in a predictable manner.
            self.array[begin:top] = np.ma.maximum(0.0,altitude[begin:top]-altitude[begin])
            self.array[top:end+1] = np.ma.maximum(0.0,altitude[top:end+1]-altitude[end])
    
    
class AltitudeForClimbCruiseDescent(DerivedParameterNode):
    units = 'ft'
    def derive(self, alt_std=P('Altitude STD')):
        self.array = hysteresis(alt_std.array, HYSTERESIS_FPALT_CCD)
    
    
class AltitudeForFlightPhases(DerivedParameterNode):
    units = 'ft'
    def derive(self, alt_std=P('Altitude STD')):
        self.array = hysteresis(repair_mask(alt_std.array), HYSTERESIS_FPALT)
    

# Q: Which of the two following AltitudeRadio's are correct?
class AltitudeRadio(DerivedParameterNode):
    """
    This function allows for the distance between the radio altimeter antenna
    and the main wheels of the undercarriage.

    The parameter raa_to_gear is measured in feet and is positive if the
    antenna is forward of the mainwheels.
    """
    units = 'ft'
    def derive(self, alt_rad=P('Altitude Radio'), pitch=P('Pitch'),
               main_gear_to_alt_rad=A('Main Gear To Altitude Radio')):
        # Align the pitch attitude samples to the Radio Altimeter samples,
        # ready for combining them.
        pitch_rad = np.radians(pitch.array)
        # Now apply the offset if one has been provided
        self.array = alt_rad.array - np.sin(pitch_rad) * main_gear_to_alt_rad.value


class AltitudeRadio(DerivedParameterNode):
    '''
    Assumes that signal (A) is at twice the frequency of (B) and (C).
    
    Therefore align to first dependency is disabled.
    
    TODO: Make this the 737-3C fram version only and await any fixes needed for other frames.
    
    '''
    align_to_first_dependency = False
    
    def derive(self, source_A=P('Altitude Radio (A)'),
               source_B=P('Altitude Radio (B)'),
               source_C=P('Altitude Radio (C)')):
        
        self.array, self.frequency, self.offset = \
            blend_two_parameters(source_B, source_C)


'''
TODO: Remove when proven to be superfluous
class AltitudeRadioForFlightPhases(DerivedParameterNode):
    def derive(self, alt_rad=P('Altitude Radio')):
        self.array = hysteresis(repair_mask(alt_rad.array), HYSTERESIS_FP_RAD_ALT)
'''


class AltitudeQNH(DerivedParameterNode):
    name = 'Altitude QNH'
    units = 'ft'
    def derive(self, param=P('Altitude AAL')):
        return NotImplemented


class AltitudeSTD(DerivedParameterNode):
    name = 'Altitude STD'
    units = 'ft'
    @classmethod
    def can_operate(cls, available):
        high_and_low = 'Altitude STD High' in available and \
            'Altitude STD Low' in available
        rough_and_ivv = 'Altitude STD Rough' in available and \
            'Inertial Vertical Speed' in available
        return high_and_low or rough_and_ivv
    
    def _high_and_low(self, alt_std_high, alt_std_low, top=18000, bottom=17000):
        # Create empty array to write to.
        alt_std = np.ma.empty(len(alt_std_high.array))
        alt_std.mask = np.ma.mask_or(alt_std_high.array.mask,
                                     alt_std_low.array.mask)
        difference = top - bottom
        # Create average of high and low. Where average is above crossover,
        # source value from alt_std_high. Where average is below crossover,
        # source value from alt_std_low.
        average = (alt_std_high.array + alt_std_low.array) / 2
        source_from_high = average > top
        alt_std[source_from_high] = alt_std_high.array[source_from_high]
        source_from_low = average < bottom
        alt_std[source_from_low] = alt_std_low.array[source_from_low]
        source_from_high_or_low = np.ma.logical_or(source_from_high,
                                                   source_from_low)
        crossover = np.ma.logical_not(source_from_high_or_low)
        crossover_indices = np.ma.where(crossover)[0]
        high_values = alt_std_high.array[crossover]
        low_values = alt_std_low.array[crossover]
        for index, high_value, low_value in zip(crossover_indices,
                                                high_values,
                                                low_values):
            average_value = average[index]
            high_multiplier = (average_value - bottom) / float(difference)
            low_multiplier = abs(1 - high_multiplier)
            crossover_value = (high_value * high_multiplier) + \
                (low_value * low_multiplier)
            alt_std[index] = crossover_value
        return alt_std
    
    def _rough_and_ivv(self, alt_std_rough, ivv):
        alt_std_with_lag = first_order_lag(alt_std_rough.array, 10,
                                           alt_std_rough.hz)
        mask = np.ma.mask_or(alt_std_with_lag.mask, ivv.array.mask)
        return np.ma.masked_array(alt_std_with_lag + (ivv.array / 60.0),
                                  mask=mask)
    
    def derive(self, alt_std_high=P('Altitude STD High'),
               alt_std_low=P('Altitude STD Low'),
               alt_std_rough=P('Altitude STD Rough'),
               ivv=P('Inertial Vertical Speed')): # Q: Is IVV name correct?
        if alt_std_high and alt_std_low:
            self.array = self._high_and_low(alt_std_high, alt_std_low)
            ##crossover = np.ma.logical_and(average > 17000, average < 18000)
            ##crossover_indices = np.ma.where(crossover)
            ##for crossover_index in crossover_indices:
                
            ##top = 18000
            ##bottom = 17000
            ##av = (alt_std_high + alt_std_low) / 2
            ##ratio = (top - av) / (top - bottom)
            ##if ratio > 1.0:
                ##ratio = 1.0
            ##elif ratio < 0.0:
                ##ratio = 0.0
            ##alt = alt_std_low * ratio + alt_std_high * (1.0 - ratio)
            ##alt_std  = alt_std * 0.8 + alt * 0.2
            
            #146-300 945003 (01) 
            #-------------------
            ##Set the thresholds for changeover from low to high scales.
            #top = 18000
            #bottom = 17000
            #
            #av = (ALT_STD_HIGH + ALT_STD_LOW) /2
            #ratio = (top - av) / (top - bottom)
            #
            #IF (ratio > 1.0) THEN ratio = 1.0 ENDIF
            #IF (ratio < 0.0) THEN ratio = 0.0 ENDIF
            #
            #alt = ALT_STD_LOW * ratio + ALT_STD_HIGH * (1.0 - ratio)
            #
            ## Smoothing to reduce unsightly noise in the signal. DJ
            #ALT_STDC = ALT_STDC * 0.8 + alt * 0.2
        elif alt_std_rough and ivv:
            self.array = self._rough_and_ivv(alt_std_rough, ivv)
            #ALT_STDC = (last_alt_std * 0.9) + (ALT_STD * 0.1) + (IVVR / 60.0)


class AltitudeTail(DerivedParameterNode):
    """
    This function allows for the distance between the radio altimeter antenna
    and the point of the airframe closest to tailscrape.
   
    The parameter gear_to_tail is measured in feet and is the distance from 
    the main gear to the point on the tail most likely to scrape the runway.
    """
    units = 'ft'
    #TODO: Review availability of Attribute "Dist Gear To Tail"
    def derive(self, alt_rad = P('Altitude Radio'), 
               pitch = P('Pitch'),
               dist_gear_to_tail=A('Dist Gear To Tail')):
        # Align the pitch attitude samples to the Radio Altimeter samples,
        # ready for combining them.
        pitch_rad= np.radians(pitch.array)
        # Now apply the offset
        self.array = alt_rad.array - np.sin(pitch_rad) * dist_gear_to_tail.value
        

class ClimbForFlightPhases(DerivedParameterNode):
    #TODO: Optimise with numpy operations
    def derive(self, alt_std=P('Altitude STD'), airs=S('Fast')):
        self.array = np.ma.zeros(len(alt_std.array))
        repair_mask(alt_std.array) # Remove small sections of corrupt data
        for air in airs:
            ax = air.slice
            # Initialise the tracking altitude value
            curr_alt = alt_std.array[ax][0]
            self.array[ax][0] = 0.0
            for count in xrange(1, int(ax.stop - ax.start)):
                if alt_std.array[ax][count] < alt_std.array[ax][count-1]:
                    # Going down, keep track of current altitude
                    curr_alt = alt_std.array[ax][count]
                    self.array[ax][count] = 0.0
                else:
                    self.array[ax][count] = alt_std.array[ax][count] - curr_alt
    
    
class DistanceTravelled(DerivedParameterNode):
    "Distance travelled in Nautical Miles. Calculated using Groundspeed"
    units = 'nm'
    #Q: could be validated using the track flown or distance between origin 
    # and destination
    def derive(self, gspd=P('Groundspeed')):
        return NotImplemented


class DistanceToLanding(DerivedParameterNode):
    units = 'nm'
    # Q: Is this distance to final landing, or distance to each approach
    # destination (i.e. resets once reaches point of go-around)
    def derive(self, dist=P('Distance Travelled'), tdwns=KTI('Touchdown')):
               ##ils_gs=P('Glideslope Deviation'),
               ##ldg=P('LandingAirport')):
        return NotImplemented


class Eng_EGTAvg(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) EGT Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) EGT'),
               eng2=P('Eng (2) EGT'),
               eng3=P('Eng (3) EGT'),
               eng4=P('Eng (4) EGT')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_EGTMax(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) EGT Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) EGT'),
               eng2=P('Eng (2) EGT'),
               eng3=P('Eng (3) EGT'),
               eng4=P('Eng (4) EGT')):
        eng = vstack_params(eng1, eng2, eng3, eng4)
        self.array = eng.max(axis=0)


class Eng_EGTMin(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) EGT Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) EGT'),
               eng2=P('Eng (2) EGT'),
               eng3=P('Eng (3) EGT'),
               eng4=P('Eng (4) EGT')):
        eng = vstack_params(eng1, eng2, eng3, eng4)
        self.array = eng.min(axis=0)


class Eng_EPRMax(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) EPR Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) EPR'),
               eng2=P('Eng (2) EPR'),
               eng3=P('Eng (3) EPR'),
               eng4=P('Eng (4) EPR')):
        eng = vstack_params(eng1, eng2, eng3, eng4)
        self.array = eng.max(axis=0)


class Eng_EPRMin(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) EPR Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) EPR'),
               eng2=P('Eng (2) EPR'),
               eng3=P('Eng (3) EPR'),
               eng4=P('Eng (4) EPR')):
        eng = vstack_params(eng1, eng2, eng3, eng4)
        self.array = eng.min(axis=0)


class EngFuelFlow(DerivedParameterNode):
    name = "Eng (*) EPR Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
            
    def derive(self, 
               eng1=P('Eng (1) Fuel Flow'),
               eng2=P('Eng (2) Fuel Flow'),
               eng3=P('Eng (3) Fuel Flow'),
               eng4=P('Eng (4) Fuel Flow')):
        eng = vstack_params(eng1, eng2, eng3, eng4)
        np.ma.sum(eng)
        pass


class Eng_N1Avg(DerivedParameterNode):
    name = "Eng (*) N1 Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
    
    def derive(self, 
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_N1Max(DerivedParameterNode):
    name = "Eng (*) N1 Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
    
    def derive(self, 
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        
        
class Eng_N1Min(DerivedParameterNode):
    name = "Eng (*) N1 Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
    
    def derive(self, 
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_N2Avg(DerivedParameterNode):
    name = "Eng (*) N2 Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_N2Max(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) N2 Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
    
    def derive(self, 
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_N2Min(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) N2 Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
    
    def derive(self, 
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_OilTempAvg(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Oil Temp Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) Oil Temp'),
               eng2=P('Eng (2) Oil Temp'),
               eng3=P('Eng (3) Oil Temp'),
               eng4=P('Eng (4) Oil Temp')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        

class Eng_OilTempMin(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Oil Temp Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) Oil Temp'),
               eng2=P('Eng (2) Oil Temp'),
               eng3=P('Eng (3) Oil Temp'),
               eng4=P('Eng (4) Oil Temp')):
        eng = vstack_params(eng1, eng2, eng3, eng4)
        self.array = eng.min(axis=0)


class Eng_OilTempMax(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Oil Temp Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) Oil Temp'),
               eng2=P('Eng (2) Oil Temp'),
               eng3=P('Eng (3) Oil Temp'),
               eng4=P('Eng (4) Oil Temp')):
        eng = vstack_params(eng1, eng2, eng3, eng4)
        self.array = eng.max(axis=0)


class Eng_OilPressAvg(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Oil Press Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) Oil Press'),
               eng2=P('Eng (2) Oil Press'),
               eng3=P('Eng (3) Oil Press'),
               eng4=P('Eng (4) Oil Press')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        
        
class Eng_OilPressMax(DerivedParameterNode):
    #TODO: TEST
    #Q: Press or Pressure?
    name = "Eng (*) Oil Press Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) Oil Press'),
               eng2=P('Eng (2) Oil Press'),
               eng3=P('Eng (3) Oil Press'),
               eng4=P('Eng (4) Oil Press')):
        eng = vstack_params(eng1, eng2, eng3, eng4)
        self.array = eng.max(axis=0)


class Eng_OilPressMin(DerivedParameterNode):
    name = 'Eng (*) Oil Press Min'
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) Oil Press'),
               eng2=P('Eng (2) Oil Press'),
               eng3=P('Eng (3) Oil Press'),
               eng4=P('Eng (4) Oil Press')):
        eng = vstack_params(eng1, eng2, eng3, eng4)
        self.array = eng.min(axis=0)


class Eng_TorqueMin(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Torque Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) Torque'),
               eng2=P('Eng (2) Torque'),
               eng3=P('Eng (3) Torque'),
               eng4=P('Eng (4) Torque')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_TorqueMax(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Torque Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) Torque'),
               eng2=P('Eng (2) Torque'),
               eng3=P('Eng (3) Torque'),
               eng4=P('Eng (4) Torque')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_VibN1Max(DerivedParameterNode):
    name = 'Eng (*) Vib N1 Max'
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) Vib N1'),
               eng2=P('Eng (2) Vib N1'),
               eng3=P('Eng (3) Vib N1'),
               eng4=P('Eng (4) Vib N1')):
        eng = vstack_params(eng1, eng2, eng3, eng4)
        self.array = eng.max(axis=0)
        
        
class Eng_VibN2Max(DerivedParameterNode):
    name = 'Eng (*) Vib N2 Max'
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) Vib N2'),
               eng2=P('Eng (2) Vib N2'),
               eng3=P('Eng (3) Vib N2'),
               eng4=P('Eng (4) Vib N2')):
        eng = vstack_params(eng1, eng2, eng3, eng4)
        self.array = eng.max(axis=0)


class FuelQty(DerivedParameterNode):
    '''
    May be replaced by an LFL parameter of the same name if available.
    
    Sum of fuel in left, right and middle tanks where available.
    '''
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
    
    def derive(self, 
               fuel_qty1=P('Fuel Qty (1)'),
               fuel_qty2=P('Fuel Qty (2)'),
               fuel_qty3=P('Fuel Qty (3)')):
        # Repair array masks to ensure that the summed values are not too small
        # because they do not include masked values.
        for param in filter(bool, [fuel_qty1, fuel_qty2, fuel_qty3]):
            param.array = repair_mask(param.array)
        stacked_params = vstack_params(fuel_qty1, fuel_qty2, fuel_qty3)
        self.array = np.ma.sum(stacked_params, axis=0)


class FlapStepped(DerivedParameterNode):
    """
    Steps raw Flap angle into chunks.

    common_steps = (0, 5, 10, 15, 20, 35, 40, 45)
    """
    @classmethod
    def can_operate(cls, available):
        if 'Flap' in available:
            return True
        
    def derive(self, flap=P('Flap'), flap_steps=A('Flap Settings')):
        if flap_steps:
            # for the moment, round off to the nearest 5 degrees
            steps = np.ediff1d(flap_steps.value, to_end=[0])/2.0 + flap_steps.value
            flap_stepped = np.zeros_like(flap.array.data)
            low = None
            for level, high in zip(flap_steps.value, steps):
                flap_stepped[(low < flap.array) & (flap.array <= high)] = level
                low = high
            else:
                # all flap values above the last
                flap_stepped[low < flap.array] = level
            self.array = np.ma.array(flap_stepped, mask=flap.array.mask)
        else:
            # round to nearest 5 degrees for the moment
            step = 5.0  # must be a float
            self.array = np.ma.round(flap.array / step) * step
            
    
class SlatStepped(DerivedParameterNode):
    """
    Steps raw Slat angle into chunks.
    """
    def derive(self, flap=P('Slat')):
        return NotImplemented


class GearSelectedDown(DerivedParameterNode):
    # And here is where the nightmare starts.
    # Sometimes recorded
    # Sometimes interpreted from other signals
    # There's no pattern to how this is worked out.
    # For aircraft with a Gear Selected Down parameter let's try this...
    def derive(self, param=P('Gear Selected Down FDR')):
        return NotImplemented


class GearSelectedUp(DerivedParameterNode):
    def derive(self, param=P('Gear Selected Up FDR')):
        pass
    
"""
Not needed for 737-3C Frame
"""
class GroundspeedAlongTrack(DerivedParameterNode):
    # Inertial smoothing provides computation of groundspeed data when the
    # recorded groundspeed is unreliable. For example, during sliding motion
    # on a runway during deceleration. This is not good enough for long
    # period computation, but is an improvement over aircraft where the 
    # groundspeed data stops at 40kn or thereabouts.
    def derive(self, gndspd=P('Ground Speed'),
               at=P('Acceleration Along Track'),
               
               
               alt_aal=P('Altitude AAL'),
               glide = P('ILS Glideslope'),


               ):
        at_washout = first_order_washout(at.array, AT_WASHOUT_TC, gndspd.hz, 
                                         gain=GROUNDSPEED_LAG_TC*GRAVITY_METRIC)
        self.array = first_order_lag(gndspd.array*KTS_TO_MPS + at_washout,
                                     GROUNDSPEED_LAG_TC,gndspd.hz)
    
        
        #-------------------------------------------------------------------
        # TEST OUTPUT TO CSV FILE FOR DEBUGGING ONLY
        # TODO: REMOVE THIS SECTION BEFORE RELEASE
        #-------------------------------------------------------------------
        import csv
        spam = csv.writer(open('beans.csv', 'wb'))
        spam.writerow(['at', 'gndspd', 'at_washout', 'self', 'alt_aal','glide'])
        for showme in range(0, len(at.array)):
            spam.writerow([at.array.data[showme], 
                           gndspd.array.data[showme]*KTS_TO_FPS,
                           at_washout[showme], 
                           self.array.data[showme],
                           alt_aal.array[showme],glide.array[showme]])
        #-------------------------------------------------------------------
        # TEST OUTPUT TO CSV FILE FOR DEBUGGING ONLY
        # TODO: REMOVE THIS SECTION BEFORE RELEASE
        #-------------------------------------------------------------------
        

class HeadingContinuous(DerivedParameterNode):
    """
    For all internal computing purposes we use this parameter which does not
    jump as it passes through North. To recover the compass display, modulus
    ("%360" in Python) returns the value to display to the user.
    """
    units = 'deg'
    def derive(self, head_mag=P('Heading')):
        self.array = repair_mask(straighten_headings(head_mag.array))


class HeadingMagnetic(DerivedParameterNode):
    '''
    This class currently exists only to give the 146-301 Magnetic Heading.
    '''
    units = 'deg'
    def derive(self, head_mag=P('RECORDED MAGNETIC HEADING')):
        self.array = head_mag.array


class HeadingTrue(DerivedParameterNode):
    units = 'deg'
    # Computes magnetic deviation linearly changing from the deviation at
    # the origin to the destination.
    def derive(self, head=P('Heading Continuous'),
               #airbornes=S('Airborne'),
               liftoffs=KTI('Liftoff'),
               takeoff_airport=A('FDR Takeoff Airport'),
               approaches=A('FDR Approaches'),
               start_datetime=A('Start Datetime')):
        
        
        #"Hard wired" for Bergen !!!!!!!!!!!!!!!!!!!
        self.array = head.array - 1.185
        
        """
        # We copy the masked array to transfer the mask array. All the data
        # values will be overwritten, but the mask will not be affected by
        # conversion from magnetic to true headings.
        true_array = np.ma.copy(head.array)        
        start_dt = start_datetime.value
        first_liftoff = liftoffs.get_first()
        if not takeoff_airport.value or \
           not takeoff_airport.value['magnetic_variation'] or not \
           approaches.value or not first_liftoff:
            self.array.mask = True
            return
        orig_index = first_liftoff.index
        orig_mag_var = takeoff_airport.value['magnetic_variation']
        variations = []
        
        for approach in approaches.value:
            dest_index = index_of_datetime(start_dt, approach['datetime'],
                                           self.frequency)
            dest_mag_var = approach['airport'].get('magnetic_variation')
            if not dest_mag_var:
                logging.warning("Cannot calculate '%s' with a missing magnetic "
                                "variation for airport with ID '%s'.",
                                self.name, approach['airport']['id'])
                self.array.mask = True
                return
            variations.append({'slice': slice(orig_index, dest_index),
                               'orig_mag_var': orig_mag_var,
                               'dest_mag_var': dest_mag_var})
            orig_index = dest_index
            orig_mag_var = dest_mag_var
        
        start_index = 0
        for variation in variations:
            orig_mag_var = variation['orig_mag_var']
            dest_mag_var = variation['dest_mag_var']
            variation_slice = variation['slice']
            
            orig_slice = slice(start_index, variation_slice.start)
            true_array[orig_slice] = head.array[orig_slice] + orig_mag_var
            mag_var_diff = dest_mag_var - orig_mag_var
            variation_duration = variation_slice.stop - variation_slice.start
            step = mag_var_diff / variation_duration
            true_array[variation_slice] = head.array[variation_slice] + \
                np.arange(orig_mag_var, dest_mag_var, step)
            start_index = variation_slice.stop
        # Apply landing airport magnetic varation to remainder of array.
        end_slice = slice(start_index, None)
        true_array[end_slice] = true_array[end_slice] + dest_mag_var
        self.array = true_array
        """

"""
class LatitudeAdjustToILS(DerivedParameterNode):
    name="Latitude Adjusted To ILS"
    def derive(self, ils_loc = P('ILS Localizer'),
               lat = P('Latitude'),
               lon = P('Longitude'),
               on_locs = S('ILS Localizer Established')):
        # We can be on the ILS during an approach leading to a landing or a go-around.
        #if (Precisiong Positioning) thingy
        for on_loc in on-locs:
"""            
       

class ILSRange(DerivedParameterNode):
    name = "ILS Range"
    
    """
    Range is computed from the track where available, otherwise estimated
    from available groundspeed or airspeed parameters.
    """
    
    def derive(self, lat=P('Latitude'),
               lon = P('Longitude'),
               glide = P('ILS Glideslope'),
               gspd = P('Groundspeed'),
               tas = P('Airspeed True'),
               alt_aal = P('Altitude AAL'),
               gs_established = S('ILS Glideslope Established'),
               airport_thing = A('Airport Data of some sort TODO put this straight')):
        
        for ils_app in 'ILS Approach Phase':

            if 'Precision':
                # Convert (repaired) latitude & longitude for the whole phase
                # into range from the threshold.
                # threshold{} = airport_thing.value[]
                brg, ils_range = bearings_and_distances(repair_mask(lat[ils_app]),
                                                        repair_mask(lon[ils_app]),
                                                        threshold)
            else:
                if 'groundspeed available':
                    # Estimate range by integrating back from zero at the end
                    # of the phase to high range values at the start of the
                    # phase.
                    speed_signal = gspd.array
                else:
                    # Estimate range using true airspeed as above.
                    speed_signal = tas.array
                    
                for this_gs in gs_established:
                    # Check we don't go below 100ft as the glideslope
                    # becomes meaningless in the last few samples.
                    slope = this_gs.slice
                    slope.stop = index_at_value(
                        alt_aal.array, 100, _slice(slope.stop,slope.start,-1))
                    
                    ils_range = integrate(repair_mask(speed_signal[slope]), 
                                          scale=KTS_TO_FPS, 
                                          direction='reverse')
                    
                    #(Q: Do we have aircraft with ILS but no groundspeed??)
            
                if 'Glideslope Established':
                    pass
                    th_dist, gs_slope, gs_gain = \
                        gs_estimate(ils_range, alt_aal.array[slope], glide[slope])
                    
                    # Compute best fit glidepath for the period from start of
                    # Glideslope Established phase to 100ft (NOT below) - use
                    # 0ft reference at antenna position (projected onto
                    # runway centreline) and the glidepath slope given in
                    # airport database for this step.
                    
                    # From the computed best fit line, find the error between
                    # the range at the threshold (localizer to threshold
                    # distance) and the range at the 50ft point on the best
                    # fit glidepath.
                else:
                    if 'runway has ILS glideslope antenna':
                        pass
                        # Compute range based upon descent path at the ILS
                        # glidepath angle to minimum point of approach.
                    else:
                        # Assume crossing runway threshold at 50ft.
                        pass
                        

                # Adjust all range values to match the datum point by
                # subtracting the range error from all values.
        
        return NotImplemented
    
    
class LatitudeAdjusted(DerivedParameterNode):
    def derive(self, lat = P('Latitude'),
               lon = P('Longitude'),
               loc_est = S('ILS Localizer Established'),
               range = P('ILS Range'),
               alt_aal = P('Altitude AAL'),
               gspd = P('Groundspeed'),
               tas = P('Airspeed True'),
               ldg_rwy = A('Landing Runway ??'),
               ):
        
        # As Longitude Adjusted
        
        return NotImplemented
          
          
class LongitudeAdjusted(DerivedParameterNode):
    def derive(self, lat = P('Latitude'),
               lon = P('Longitude'),
               loc_est = S('ILS Localizer Established'),
               range = P('ILS Range'),
               alt_aal = P('Altitude AAL'),
               gspd = P('Groundspeed'),
               tas = P('Airspeed True'),
               ldg_rwy = A('Landing Runway ??'),
               ):

        for app in 'ILS Localizer Established':
            continue
            # Recover previously computed range from the threshold and
            # convert to range from the localizer.
            
            # Join with ILS bearings (inherently from the localizer) and
            # revert the ILS track from range and bearing to lat & long
            # coordinates.

        if 'Precision':
            # No need to refine takeoff or landing plots other than using
            # localizer covered above, so finish now.
            pass
        
        else:
            # We can improve the track using a variety of techniques.

            # --- Takeoff Track ---
            
            if 'groundspeed available':
                # Compute takeoff track from start of runway using
                # integrated groundspeed, down runway centreline to point
                # of liftoff.
                pass
            else:
                # Compute takeoff track from start of runway using
                # integrated true airspeed down runway centreline to
                # point of liftoff.
                pass
    
            # --- Landing Track ---
            
            if not 'Localizer Established Approach':
                if 'groundspeed available':
                    # Compute landing track from 50ft AAL over threshold
                    # using integrated groundspeed, down runway
                    # centreline.
                    pass

                else:
                    # Compute landing track from 50ft AAL over threshold
                    # using integrated true airspeed, down runway
                    # centreline.
                    pass

        # --- Merge Tracks ---
                
        # Obtain corrected tracks from takeoff phase, final
        # approach and landing phase and possible
        # intermediate approach and go-around phases, and
        # compute error terms to align the recorded lat&long
        # with each partial data segment. This is done by
        # computing linearly varying adjustment factors
        # between each computed section.


        return NotImplemented
          
          
class MACH(DerivedParameterNode):
    def derive(self, ias = P('Airspeed'), tat = P('TAT'),
               alt = P('Altitude Std')):
        return NotImplemented
        

class RateOfClimb(DerivedParameterNode):
    """
    This routine derives the rate of climb from the vertical acceleration, the
    Pressure altitude and the Radio altitude.
    
    We use pressure altitude rate above 100ft and radio altitude rate below
    50ft, with a progressive changeover across that range. Below 100ft the
    pressure altitude information is affected by the flow field around the
    aircraft, while above 50ft there is an increasing risk of changes in
    ground profile affecting the radio altimeter signal.
    
    Complementary first order filters are used to combine the acceleration
    data and the height data. A high pass filter on the altitude data and a
    low pass filter on the acceleration data combine to form a consolidated
    signal.
    
    By merging the altitude rate signals, we avoid problems of altimeter
    datums affecting the transition as these will have been washed out by the
    filter stage first.
    
    Long term errors in the accelerometers are removed by washing out the
    acceleration term with a longer time constant filter before use. The
    consequence of this is that long period movements with continued
    acceleration will be underscaled slightly. As an example the test case
    with a 1ft/sec^2 acceleration results in an increasing rate of climb of
    55 fpm/sec, not 60 as would be theoretically predicted.
    """
    # List the minimum acceptable parameters here
    @classmethod
    def can_operate(cls, available):
        # List the minimum required parameters. If 'Altitude Radio For Flight
        # Phases' is available, that's a bonus and we will use it, but it is
        # not required.
        return 'Altitude STD' in available
    
    def derive(self, 
               az = P('Acceleration Vertical'),
               alt_std = P('Altitude STD'),
               alt_rad = P('Altitude Radio'),
               pitch=P('Pitch'),
               aoa=P('AOA'),
               speed=P('Airspeed')):

        if az and alt_rad:
            # Make space for the answers
            self.array = np.ma.masked_all_like(alt_std.array)
            
            # Fix minor dropouts
            az_repair = repair_mask(az.array)
            alt_rad_repair = repair_mask(alt_rad.array)
            alt_std_repair = repair_mask(alt_std.array)
            
            # np.ma.getmaskarray ensures we have complete mask arrays even if
            # none of the samples are masked (normally returns a single
            # "False" value.
            az_masked = np.ma.array(data = az_repair.data, 
                                    mask = np.ma.logical_or(
                                        np.ma.logical_or(
                                        np.ma.getmaskarray(az_repair),
                                        np.ma.getmaskarray(alt_rad_repair)),
                                        np.ma.getmaskarray(alt_std_repair)))
            
            # We are going to compute the answers only for ranges where all
            # the required parameters are available.
            clumps = np.ma.clump_unmasked(az_masked)
            for clump in clumps:
                
                # Use the complementary smoothing approach
    
                roc_alt_std = first_order_washout(alt_std.array[clump],
                                                  RATE_OF_CLIMB_LAG_TC, az.hz,
                                                  gain=1/RATE_OF_CLIMB_LAG_TC)
                roc_alt_rad = first_order_washout(alt_rad.array[clump],
                                                  RATE_OF_CLIMB_LAG_TC, az.hz,
                                                  gain=1/RATE_OF_CLIMB_LAG_TC)
                        
                # Use pressure altitude rate above 100ft and radio altitude rate
                # below 50ft with progressive changeover across that range.
                # up to 50 ft radio 0 < std_rad_ratio < 1 over 100ft radio
                std_rad_ratio = np.maximum(np.minimum(
                    (alt_rad.array.data[clump]-50.0)/50.0,
                    1),0)
                roc_altitude = roc_alt_std*std_rad_ratio +\
                    roc_alt_rad*(1.0-std_rad_ratio)
                
                # This is the washout term, with considerable gain. The
                # initialisation "initial_value=az.array[clump][0]" is very
                # important, as without this the function produces huge
                # spikes at each start of a data period.
                az_washout = first_order_washout (az.array[clump], 
                                                  AZ_WASHOUT_TC, az.hz, 
                                                  gain=GRAVITY_IMPERIAL,
                                                  initial_value=az.array[clump][0])
                inertial_roc = first_order_lag (az_washout, 
                                                RATE_OF_CLIMB_LAG_TC, 
                                                az.hz, 
                                                gain=RATE_OF_CLIMB_LAG_TC)
                self.array[clump] = (roc_altitude + inertial_roc) * 60.0
            
        else:
            # The period for averaging altitude only data has been chosen
            # from careful inspection of Hercules data, where the pressure
            # altitude signal resolution is of the order of 9 ft/bit.
            # Extension to wider timebases, or averaging with more samples,
            # smooths the data more but equally more samples are affected by
            # corrupt source data. So, change the "3" only after careful
            # consideration.
            self.array = rate_of_change(alt_std,3)*60
         
         
class RateOfClimbForFlightPhases(DerivedParameterNode):
    def derive(self, alt_std = P('Altitude STD'),
               fast = S('Fast')):
        # This uses a scaled hysteresis parameter. See settings for more detail.
        for speedy in fast:
            threshold = HYSTERESIS_FPROC * \
                max(1, rms_noise(alt_std.array[speedy.slice]))  
            # The max(1, prevents =0 case when testing with artificial data.
            self.array = hysteresis(rate_of_change(repair_mask(alt_std),3)*60,
                                    threshold)


class Relief(DerivedParameterNode):
    # also known as Terrain
    
    # Quickly written without tests as I'm really editing out the old dependencies statements :-(
    def derive(self, alt_aal = P('Altitude AAL'),
               alt_rad = P('Radio Altitude')):
        self.array = alt_aal - alt_rad


class Speedbrake(DerivedParameterNode):
    def derive(self, param=P('Speedbrake FDR')):
        # There will be a recorded parameter, but varying types of correction will 
        # need to be applied according to the aircraft type and data frame.
        self.array = param


class CoordinatesSmoothed(object):
    '''
    Superclass for LatitudeSmoothed and LongitudeSmoothed.
    '''
    units = 'deg'
    def _smooth_coordinates(self, coord1, coord2):
        """
        Acceleration along track only used to determine the sample rate and
        alignment of the resulting smoothed track parameter.
        
        :param coord1: Either 'Latitude' or 'Longitude' parameter.
        :type coord1: DerivedParameterNode
        :param coord2: Either 'Latitude' or 'Longitude' parameter.
        :type coord2: DerivedParameterNode
        :returns: coord1 smoothed.
        :rtype: np.ma.masked_array
        """        
        coord1_s = repair_mask(coord1.array)
        coord2_s = repair_mask(coord2.array)
        
        # Join the masks, so that we only consider positional data when both are valid:
        coord1_s.mask = np.ma.logical_or(np.ma.getmaskarray(coord1_s),
                                         np.ma.getmaskarray(coord2_s))
        coord2_s.mask = coord1_s.mask
        # Preload the output with masked values to keep dimension correct 
        array = coord1_s  
        
        # Now we just smooth the valid sections.
        tracks = np.ma.clump_unmasked(coord1_s)
        for track in tracks:
            coord1_s_track, coord2_s_track, cost = \
                smooth_track(coord1.array[track], coord2.array[track])
            array[track] = coord1_s_track
        return array
        

class LatitudeSmoothed(DerivedParameterNode, CoordinatesSmoothed):
    def derive(self, acc_fwd=P('Acceleration Along Track'),
               lat=P('Latitude Adjusted'), lon=P('Longitude Adjusted')):
        """
        Acceleration along track only used to determine the sample rate and
        alignment of the resulting smoothed track parameter.
        """
        self.array = self._smooth_coordinates(lat, lon)

    
class LongitudeSmoothed(DerivedParameterNode, CoordinatesSmoothed):
    def derive(self, acc_fwd=P('Acceleration Along Track'),
               lat=P('Latitude Adjusted'), lon=P('Longitude Adjusted')):
        self.array = self._smooth_coordinates(lon, lat)


class RateOfTurn(DerivedParameterNode):
    def derive(self, head=P('Heading Continuous')):
        self.array = rate_of_change(head, 1)


class Pitch(DerivedParameterNode):
    units = 'deg'
    def derive(self, p1=P('Pitch (1)'), p2=P('Pitch (2)')):
        self.hz = p1.hz * 2
        self.offset = min(p1.offset, p2.offset)
        self.array = interleave (p1, p2)


class PitchRate(DerivedParameterNode):
    # TODO: Tests.
    def derive(self, pitch=P('Pitch')):
        # TODO: What should half_width be?
        self.array = rate_of_change(pitch, 1)


class ThrottleLever(DerivedParameterNode):
    def derive(self, tla1=P('Throttle Lever Angle (1)'), 
               tla2=P('Throttle Lever Angle (2)')):
        ##self.hz = tla1.hz * 2
        ##self.offset = min(tla1.offset, tla2.offset)
        ##self.array = interleave (tla1, tla2)
        return NotImplemented

