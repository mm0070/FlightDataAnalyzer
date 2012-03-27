import logging
import numpy as np
from tempfile import TemporaryFile
from operator import attrgetter

#from aerocalc.airspeed import cas2tas, cas2dp, cas_alt2mach, mach2temp, dp2tas
    
from analysis_engine.model_information import (get_aileron_map, 
                                               get_config_map,
                                               get_flap_map,
                                               get_slat_map)
from analysis_engine.node import A, DerivedParameterNode, KPV, KTI, P, S, Parameter

from analysis_engine.library import (align,
                                     bearings_and_distances,
                                     blend_alternate_sensors,
                                     blend_two_parameters,
                                     clip,
                                     coreg,
                                     first_order_lag,
                                     first_order_washout,
                                     hysteresis,
                                     index_at_value,
                                     index_of_datetime,
                                     integrate,
                                     interleave,
                                     ils_localizer_align,
                                     is_index_within_slice,
                                     is_slice_within_slice,
                                     latitudes_and_longitudes,
                                     merge_sources,
                                     merge_two_parameters,
                                     rate_of_change, 
                                     repair_mask,
                                     rms_noise,
                                     round_to_nearest,
                                     runway_distances,
                                     runway_heading,
                                     runway_length,
                                     slices_overlap,
                                     smooth_track,
                                     step_values,
                                     straighten_headings,
                                     track_linking,
                                     value_at_index,
                                     vstack_params,
                                     
                                     alt2press,
                                     alt2press_ratio,
                                     alt2sat,
                                     cas2dp,
                                     cas_alt2mach,
                                     dp_over_p2mach,
                                     dp2tas,
                                     mach2temp,
                                     _alt2press_ratio_gradient,
                                     _alt2press_ratio_isothermal)

from settings import (AIRSPEED_THRESHOLD,
                      ALTITUDE_AAL_LAG_TC,
                      AZ_WASHOUT_TC,
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
                      METRES_TO_FEET,
                      RATE_OF_CLIMB_LAG_TC,
                      TRANSITION_ALT_RAD_TO_STD,
                      )


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
        return 'Airspeed' in available or 'Acceleration Longitudinal' in available
        
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


class AirspeedMinusV2For3Sec(DerivedParameterNode):
    #TODO: TESTS
    def derive(self, spd_v2=P('AirspeedMinusV2')):
        self.array = clip(spd_v2.array, 3.0, spd_v2.frequency)
        

class AirspeedMinusV2For5Sec(DerivedParameterNode):
    #TODO: TESTS
    def derive(self, airspeed=P('AirspeedMinusV2')):
        self.array = clip(spd_v2.array, 5.0, spd_v2.frequency)
        

class AirspeedMinusVref(DerivedParameterNode):
    #TODO: TESTS
    def derive(self, airspeed=P('Airspeed'), vref=A('FDR Vref')):
        self.array = airspeed.array - vref.value


class AirspeedMinusVrefFor3Sec(DerivedParameterNode):
    #TODO: TESTS
    def derive(self, spd_vref=P('Airspeed Minus Vref')):
        self.array = clip(spd_vref.array, 3.0, spd_vref.frequency)

        
class AirspeedMinusVrefFor5Sec(DerivedParameterNode):
    #TODO: TESTS
    def derive(self, spd_vref=P('Airspeed Minus Vref')):
        self.array = clip(spd_vref.array, 5.0, spd_vref.frequency)

        
class AirspeedTrue(DerivedParameterNode):
    @classmethod
    def can_operate(cls, available):
        return 'Airspeed' in available and 'Altitude STD' in available
    
    """
    def derive(self, cas = P('Airspeed'),
               alt_std = P('Altitude STD'),
               tat = P('TAT')):
        '''
        Uses AeroCalc library for conversion of airspeed data
        '''
        # Prepare a list for the answers
        tas=[]
        # Compute each value in turn, using only the data elements
        if tat:
            for i in range(len(cas.array)):
                dp = cas2dp(cas.array.data[i])
                mach = cas_alt2mach(cas.array.data[i], alt_std.array.data[i])
                temp = mach2temp(mach, tat.array.data[i], 1.0)
                tas.append(dp2tas(dp,alt_std.array.data[i],temp))
            
            # Each value is invalid if any of the three components is masked
            combined_mask= np.logical_or(
                np.logical_or(cas.array.mask,alt_std.array.mask),tat.array.mask)
        else:
            # The Hercules "worst case" has no air temperature recorded
            for i in range(len(cas.array)):
                tas.append(cas2tas(cas.array.data[i],alt_std.array.data[i])) 
                # Assumes ISA temperatures
            combined_mask= np.logical_or(cas.array.mask,alt_std.array.mask)
                           
        # Combine the data and mask to finish the job.
        self.array = np.ma.array(data=tas, mask=combined_mask)
        """
    #---------------------------------------------------------------------------
    # Derive method
    #---------------------------------------------------------------------------

    def derive(self, cas_p = P('Airspeed'),
               alt_std_p = P('Altitude STD'),
               tat_p = P('TAT')):
        cas = cas_p.array
        alt_std = alt_std_p.array
        if tat_p:
            tat = tat_p.array
            dp = cas2dp(cas)
            mach = cas_alt2mach(cas, alt_std)
            sat = mach2temp(mach, tat)
            tas = dp2tas(dp, alt_std, sat)
            combined_mask= np.logical_or(
                np.logical_or(cas_p.array.mask,alt_std_p.array.mask),
                tas.mask)
        else:
            dp = cas2dp(cas)
            sat = alt2sat(alt_std)
            tas = dp2tas(dp, alt_std, sat)
            combined_mask= np.logical_or(cas_p.array.mask,alt_std_p.array.mask)
            
        # This output format puts zero values where the TAS is invalid, makeing
        # inspection using HDF viewer more convenient.
        self.array = np.ma.array(data=np.where(combined_mask, 0.0, tas),
                                 mask=combined_mask)
        

class AltitudeAAL(DerivedParameterNode):
    """
    Altitude Parameter to account for transition altitudes for airports
    between "altitude above mean sea level" and "pressure altitude relative
    to FL100". Ideally use the BARO selection switch when recorded, else the
    Airport elevation where provided, else guess based on location (USA =
    18,000ft, Europe = 3,000ft)

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
        #TODO: Improve accuracy of this method. For example, the code does
        #not cater for the availability of Altitude Radio but Rate Of Climb
        #not being available.
        smoothing_params = all([d in available for d in ('Liftoff',
                                                         'Touchdown',
                                                         'Takeoff',
                                                         'Landing',
                                                         'Rate Of Climb',
                                                         'Altitude STD',
                                                         'Altitude Radio',
                                                         'Airspeed')])
        fallback = 'Altitude AAL For Flight Phases' in available
        return smoothing_params or fallback
    
    def derive(self, liftoffs=KTI('Liftoff'),
               touchdowns=KTI('Touchdown'),
               takeoffs=S('Takeoff'),
               landings=S('Landing'),
               roc = P('Rate Of Climb'),
               alt_std = P('Altitude STD'),
               alt_rad = P('Altitude Radio'),
               airspeed = P('Airspeed'),
               alt_aal_4fp = P('Altitude AAL For Flight Phases'),):
        if liftoffs and touchdowns and landings and roc and alt_std \
           and alt_rad and airspeed:
            # Initialise the array to zero, so that the altitude above the airfield
            # will be 0ft when the aircraft cannot be airborne.
            alt_aal = np.zeros_like(alt_std.array) 
            # Actually creates a masked copy filled with zeros.
            
            ordered_ktis = sorted(liftoffs + touchdowns,
                                  key=attrgetter('index'))
            
            for next_index, first_kti in enumerate(ordered_ktis, start=1):
                # Iterating over pairs of 'Liftoff' and 'Touchdown' KTIs ordered
                # by index. Expecting Touchdowns followed by Liftoffs.
                try:
                    second_kti = ordered_ktis[next_index]
                except IndexError:
                    break
                in_air_slice = slice(first_kti.index, second_kti.index)
                
                             
                # Use pressure altitude to ensure data is filled between
                # Liftoff and Touchdown KTIs.
                alt_aal[in_air_slice] = alt_std.array[in_air_slice]
                peak_index = np.ma.argmax(alt_std.array[in_air_slice]) + \
                                        in_air_slice.start
                if first_kti.name == 'Liftoff':
                    threshold_index = index_at_value(alt_rad.array,
                                                     TRANSITION_ALT_RAD_TO_STD,
                                                     _slice=in_air_slice)
                    join_index = int(threshold_index)
                    difference = alt_rad.array[join_index] - \
                        alt_std.array[join_index]
                    alt_aal[join_index:peak_index] += difference
                    pre_threshold = slice(in_air_slice.start, join_index)
                    alt_aal[pre_threshold] = alt_rad.array[pre_threshold]
                
                if second_kti.name == 'Touchdown':
                    reverse_slice = slice(in_air_slice.stop,
                                          in_air_slice.start, -1)
                    threshold_index = index_at_value(alt_rad.array,
                                                     TRANSITION_ALT_RAD_TO_STD,
                                                     _slice=reverse_slice)
                    join_index = int(threshold_index)+1
                    difference = alt_rad.array[join_index] - \
                        alt_std.array[join_index]
                    alt_aal[peak_index:join_index] += difference
                    post_threshold = slice(join_index, in_air_slice.stop)
                    alt_aal[post_threshold] = alt_rad.array[post_threshold]
        
            # Use the complementary smoothing approach
            roc_lag = first_order_lag(roc.array,
                                      ALTITUDE_AAL_LAG_TC, roc.hz,
                                      gain=ALTITUDE_AAL_LAG_TC/60.0)            
            alt_aal_lag = first_order_lag(alt_aal, ALTITUDE_AAL_LAG_TC, roc.hz)
            alt_aal = alt_aal_lag + roc_lag
            # Force result to zero at low speed and on the ground.
            #alt_aal[airspeed.array < AIRSPEED_THRESHOLD] = 0
            #alt_aal[alt_rad.array < 0] = 0
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
    

"""
# Q: Which of the two following AltitudeRadio's are correct?
# Note: The first one cannot replace its own name (Altitude Radio) and
# therefore will never be processed?
class AltitudeRadio(DerivedParameterNode):
    '''
    This function allows for the distance between the radio altimeter antenna
    and the main wheels of the undercarriage.

    The parameter raa_to_gear is measured in feet and is positive if the
    antenna is forward of the mainwheels.
    '''
    units = 'ft'
    def derive(self, alt_rad=P('Altitude Radio'), pitch=P('Pitch'),
               main_gear_to_alt_rad=A('Main Gear To Altitude Radio')):
        # Align the pitch attitude samples to the Radio Altimeter samples,
        # ready for combining them.
        pitch_rad = np.radians(pitch.array)
        # Now apply the offset if one has been provided
        self.array = alt_rad.array - np.sin(pitch_rad) * main_gear_to_alt_rad.value
"""

class AltitudeRadio(DerivedParameterNode):
    '''
    This class allows for variations in the Altitude Radio sensor, and the
    different frame types need to be identified accordingly.

    POLARIS compensates for the apparent change in height caused by the
    aircraft pitch attitude and the distance between the radio altimeter
    antenna and the main wheels of the undercarriage. The parameter
    raa_to_gear is measured in feet and is positive if the antenna is forward
    of the mainwheels.
    '''
    @classmethod
    def can_operate(cls, available):
        if 'Altitude Radio (A)' in available and \
           'Altitude Radio (B)' in available:
            return True
    
    align_to_first_dependency = False
    
    def derive(self, source_A = P('Altitude Radio (A)'),
               source_B = P('Altitude Radio (B)'),
               source_C = P('Altitude Radio (C)'),
               frame = A('Frame'),
               main_gear_to_alt_rad = A('Main Gear To Altitude Radio'),
               pitch = P('Pitch')):
        
        frame_name = frame.value if frame else None
        if frame_name in ['737-3C']:
            # Alternate samples (A) for this frame have latency of over 1 second,
            # so do not contribute to the height measurements available.
            
            self.array, self.frequency, self.offset = \
                merge_two_parameters(source_B, source_C)
            
        elif frame_name in ['737-4', '737-4_Analogue']:
            self.array, self.frequency, self.offset = \
                merge_two_parameters(source_A, source_B)
        
        else:
            logging.warning("No specified Altitude Radio (*) merging for frame "
                            "'%s' so using source (A)", frame_name)
            self.array = source_A.array
            
        # Now apply the offset if one has been provided
        if main_gear_to_alt_rad:
            self.array -= np.sin(np.radians(pitch.array)) * main_gear_to_alt_rad.value
            

class AltitudeRadioForFlightPhases(DerivedParameterNode):
    def derive(self, alt_rad=P('Altitude Radio')):
        self.array = hysteresis(repair_mask(alt_rad.array), HYSTERESIS_FP_RAD_ALT)


class AltitudeQNH(DerivedParameterNode):
    name = 'Altitude QNH'
    units = 'ft'
    
    ''' TODO: This altitude Parameter is for events based upon height above
    sea level, not standard altitude or airfield elevation. For example, in
    the US the speed high below 10,000ft is based on height above sea level.
    Ideally use the BARO selection switch when recorded, else based upon the
    transition height for the departing airport in the climb and the arrival
    airport in the descent. If no such data is available, transition at
    18,000 ft (USA standard). because there is no European standard
    transition height.
    '''
    
    def derive(self, alt_aal=P('Altitude AAL'), 
               land = A('FDR Landing Airport'),
               toff = A('FDR Takeoff Airport')):
        # Break the "journey" at the midpoint.
        peak = np.ma.argmax(alt_aal.array)
        alt_qnh = np.ma.copy(alt_aal.array)

        # Add the elevation of the takeoff airport (above sea level) to the
        # climb portion. If this fails, make sure it's inhibited.
        try:
            alt_qnh[:peak]+=toff.value['elevation']
        except:
            alt_qnh[:peak]=np.ma.masked
        
        # Same for the downward leg of the journey.
        try:
            alt_qnh[peak:]+=land.value['elevation']
        except:
            alt_qnh[peak:]=np.ma.masked
            
        self.array = alt_qnh


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
   
    The parameter gear_to_tail is measured in metres and is the distance from 
    the main gear to the point on the tail most likely to scrape the runway.
    """
    units = 'ft'
    #TODO: Review availability of Attribute "Dist Gear To Tail"
    def derive(self, alt_rad=P('Altitude Radio'), pitch=P('Pitch'),
               ground_to_tail=A('Ground To Lowest Point Of Tail'),
               dist_gear_to_tail=A('Main Gear To Lowest Point Of Tail')):
        # Align the pitch attitude samples to the Radio Altimeter samples,
        # ready for combining them.
        pitch_rad = np.radians(pitch.array)
        # Now apply the offset
        gear2tail = dist_gear_to_tail.value * METRES_TO_FEET
        ground2tail = ground_to_tail.value * METRES_TO_FEET
        self.array = (alt_rad.array - np.sin(pitch_rad) * gear2tail)


class ClimbForFlightPhases(DerivedParameterNode):
    def derive(self, alt_std=P('Altitude STD'), airs=S('Fast')):
        self.array = np.ma.zeros(len(alt_std.array))
        repair_mask(alt_std.array) # Remove small sections of corrupt data
        for air in airs:
            deltas = np.ma.ediff1d(alt_std.array[air.slice], to_begin=0.0)
            ups = np.ma.clump_unmasked(np.ma.masked_less(deltas,0.0))
            for up in ups:
                self.array[air.slice][up] = np.ma.cumsum(deltas[up])    

            
class DescendForFlightPhases(DerivedParameterNode):
    def derive(self, alt_std=P('Altitude STD'), airs=S('Fast')):
        self.array = np.ma.zeros(len(alt_std.array))
        repair_mask(alt_std.array) # Remove small sections of corrupt data
        for air in airs:
            deltas = np.ma.ediff1d(alt_std.array[air.slice], to_begin=0.0)
            downs = np.ma.clump_unmasked(np.ma.masked_greater(deltas,0.0))
            for down in downs:
                self.array[air.slice][down] = np.ma.cumsum(deltas[down])
    
    
class ControlColumn(DerivedParameterNode):
    '''
    The position of the control column blended from the position of the captain
    and first officer's control columns.
    '''
    align_to_first_dependency = False
    def derive(self,
               posn_capt=P('Control Column (Capt)'),
               posn_fo=P('Control Column (FO)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(posn_capt, posn_fo)


class ControlColumnForceCapt(DerivedParameterNode):
    '''
    The force applied by the captain to the control column.  This is dependent
    on who has master control of the aircraft and this derived parameter
    selects the appropriate slices of data from the foreign and local forces.
    '''
    name = 'Control Column Force (Capt)'
    def derive(self,
               force_local=P('Control Column Force (Local)'),
               force_foreign=P('Control Column Force (Foreign)'),
               fcc_master=P('FCC Local Limited Master')):
        self.array = np.ma.where(fcc_master.array != 1,
                                 force_local.array,
                                 force_foreign.array)


class ControlColumnForceFO(DerivedParameterNode):
    '''
    The force applied by the first officer to the control column.  This is
    dependent on who has master control of the aircraft and this derived
    parameter selects the appropriate slices of data from the foreign and local
    forces.
    '''
    name = 'Control Column Force (FO)'
    def derive(self,
               force_local=P('Control Column Force (Local)'),
               force_foreign=P('Control Column Force (Foreign)'),
               fcc_master=P('FCC Local Limited Master')):
        self.array = np.ma.where(fcc_master.array == 1,
                                 force_local.array,
                                 force_foreign.array)


class ControlColumnForce(DerivedParameterNode):
    '''
    The combined force from the captain and the first officer.
    '''
    def derive(self,
               force_capt=P('Control Column Force (Capt)'),
               force_fo=P('Control Column Force (FO)')):
        self.array = force_capt.array + force_fo.array
        # TODO: Check this summation is correct in amplitude and phase.
        # Compare with Boeing charts for the 737NG.



class ControlWheel(DerivedParameterNode):
    '''
    The position of the control wheel blended from the position of the captain
    and first officer's control wheels.
    '''
    align_to_first_dependency = False
    def derive(self,
               posn_capt=P('Control Wheel (Capt)'),
               posn_fo=P('Control Wheel (FO)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(posn_capt, posn_fo)


class DistanceToLanding(DerivedParameterNode):
    units = 'nm'
    # Q: Is this distance to final landing, or distance to each approach
    # destination (i.e. resets once reaches point of go-around)
    def derive(self, dist=P('Distance Travelled'), tdwns=KTI('Touchdown')):
        ##ils_gs=P('Glideslope Deviation'), ##ldg=P('LandingAirport')): #
        # this version gets closer to zero as we approach the final touchdown
        # and then increases as we go past
        if tdwns:
            dist_flown_at_tdwn = dist.array[tdwns.get_last().index]
            self.array = np.ma.abs(dist_flown_at_tdwn - dist.array)
        else:
            self.array = np.zeros_like(dist.array)
            self.array.mask=True        


class DistanceTravelled(DerivedParameterNode):
    "Distance travelled in Nautical Miles. Calculated using Groundspeed"
    units = 'nm'
    #Q: could be validated using the track flown or distance between origin 
    # and destination
    def derive(self, gspd=P('Groundspeed')):
        self.array = integrate(gspd.array, gspd.frequency, scale=1.0)
        

class Eng_EGTAvg(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) EGT Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
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
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) EGT'),
               eng2=P('Eng (2) EGT'),
               eng3=P('Eng (3) EGT'),
               eng4=P('Eng (4) EGT')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_EGTMin(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) EGT Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) EGT'),
               eng2=P('Eng (2) EGT'),
               eng3=P('Eng (3) EGT'),
               eng4=P('Eng (4) EGT')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_EPRAvg(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) EPR Avg"
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
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_EPRMax(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) EPR Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) EPR'),
               eng2=P('Eng (2) EPR'),
               eng3=P('Eng (3) EPR'),
               eng4=P('Eng (4) EPR')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_EPRMin(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) EPR Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) EPR'),
               eng2=P('Eng (2) EPR'),
               eng3=P('Eng (3) EPR'),
               eng4=P('Eng (4) EPR')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_FuelFlow(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Fuel Flow"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
            
    def derive(self, 
               eng1=P('Eng (1) Fuel Flow'),
               eng2=P('Eng (2) Fuel Flow'),
               eng3=P('Eng (3) Fuel Flow'),
               eng4=P('Eng (4) Fuel Flow')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.sum(engines, axis=0)
      

class Eng_ITTAvg(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) ITT Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) ITT'),
               eng2=P('Eng (2) ITT'),
               eng3=P('Eng (3) ITT'),
               eng4=P('Eng (4) ITT')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_ITTMax(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) ITT Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) ITT'),
               eng2=P('Eng (2) ITT'),
               eng3=P('Eng (3) ITT'),
               eng4=P('Eng (4) ITT')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_ITTMin(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) ITT Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) ITT'),
               eng2=P('Eng (2) ITT'),
               eng3=P('Eng (3) ITT'),
               eng4=P('Eng (4) ITT')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_N1Avg(DerivedParameterNode):
    name = "Eng (*) N1 Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
    
    def derive(self, 
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_N1Avg(DerivedParameterNode):
    name = "Eng (*) N1 Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
    
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
        return any([d in available for d in cls.get_dependency_names()])
    
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
        return any([d in available for d in cls.get_dependency_names()])
    
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
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_N2Max(DerivedParameterNode):
    name = "Eng (*) N2 Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
            
    def derive(self, 
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_N2Min(DerivedParameterNode):
    name = "Eng (*) N2 Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
    
    def derive(self, 
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_N3Avg(DerivedParameterNode):
    name = "Eng (*) N3 Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) N3'),
               eng2=P('Eng (2) N3'),
               eng3=P('Eng (3) N3'),
               eng4=P('Eng (4) N3')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_N3Max(DerivedParameterNode):
    name = "Eng (*) N3 Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
    
    def derive(self, 
               eng1=P('Eng (1) N3'),
               eng2=P('Eng (2) N3'),
               eng3=P('Eng (3) N3'),
               eng4=P('Eng (4) N3')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_N3Min(DerivedParameterNode):
    name = "Eng (*) N3 Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
    
    def derive(self, 
               eng1=P('Eng (1) N3'),
               eng2=P('Eng (2) N3'),
               eng3=P('Eng (3) N3'),
               eng4=P('Eng (4) N3')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_OilTempAvg(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Oil Temp Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
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
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) Oil Temp'),
               eng2=P('Eng (2) Oil Temp'),
               eng3=P('Eng (3) Oil Temp'),
               eng4=P('Eng (4) Oil Temp')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_OilTempMax(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Oil Temp Max"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) Oil Temp'),
               eng2=P('Eng (2) Oil Temp'),
               eng3=P('Eng (3) Oil Temp'),
               eng4=P('Eng (4) Oil Temp')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_OilPressAvg(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Oil Press Avg"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
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
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) Oil Press'),
               eng2=P('Eng (2) Oil Press'),
               eng3=P('Eng (3) Oil Press'),
               eng4=P('Eng (4) Oil Press')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_OilPressMin(DerivedParameterNode):
    #TODO: TEST
    name = 'Eng (*) Oil Press Min'
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) Oil Press'),
               eng2=P('Eng (2) Oil Press'),
               eng3=P('Eng (3) Oil Press'),
               eng4=P('Eng (4) Oil Press')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_TorqueAvg(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Torque Avg"
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
        self.array = np.ma.average(engines, axis=0)


class Eng_TorqueMin(DerivedParameterNode):
    #TODO: TEST
    name = "Eng (*) Torque Min"
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
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
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) Torque'),
               eng2=P('Eng (2) Torque'),
               eng3=P('Eng (3) Torque'),
               eng4=P('Eng (4) Torque')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_VibN1Max(DerivedParameterNode):
    #TODO: TEST
    name = 'Eng (*) Vib N1 Max'
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) Vib N1'),
               eng2=P('Eng (2) Vib N1'),
               eng3=P('Eng (3) Vib N1'),
               eng4=P('Eng (4) Vib N1')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        
        
class Eng_VibN2Max(DerivedParameterNode):
    #TODO: TEST
    name = 'Eng (*) Vib N2 Max'
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
        
    def derive(self, 
               eng1=P('Eng (1) Vib N2'),
               eng2=P('Eng (2) Vib N2'),
               eng3=P('Eng (3) Vib N2'),
               eng4=P('Eng (4) Vib N2')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_VibN3Max(DerivedParameterNode):
    #TODO: TEST
    name = 'Eng (*) Vib N3 Max'
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) Vib N3'),
               eng2=P('Eng (2) Vib N3'),
               eng3=P('Eng (3) Vib N3'),
               eng4=P('Eng (4) Vib N3')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class FuelQty(DerivedParameterNode):
    '''
    May be replaced by an LFL parameter of the same name if available.
    
    Sum of fuel in left, right and middle tanks where available.
    '''
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return any([d in available for d in cls.get_dependency_names()])
    
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


class GearDown(DerivedParameterNode):
    align_to_first_dependency = False
    def derive(self, gl=P('GEAR DOWN LEFT'),
               gn=P('GEAR DOWN NOSE'),
               gr=P('GEAR DOWN RIGHT')):
        '''
        Highly aircraft dependent, so likely to be extended.
        '''
        # 737-5 has nose gear sampled alternately with mains. No obvious way
        # to accommodate mismatch of the main gear positions, so assume that
        # the right wheel does the same as the left !
        self.array, self.frequency, self.offset = merge_two_parameters(gl, gn)
        
        
class GrossWeightSmoothed(DerivedParameterNode):
    '''
    Gross weight is usually sampled at a low rate and can be very poor in the
    climb, often indicating an increase in weight at takeoff and this effect
    may not end until the aircraft levels in the cruise. Also some aircraft
    weight data saturates at high AUW values, and while the POLARIS Analysis
    Engine can mask this data a subsitute is needed for takeoff weight (hence
    V2) calculations. This can only be provided by extrapolation backwards
    from data available later in the flight.
    
    This routine makes the best of both worlds by using fuel flow to compute
    short term changes in weight and mapping this onto the level attitude
    data. We avoid using the recorded fuel weight in this calculation,
    however it is used in the Zero Fuel Weight calculation.
    '''
    align_to_first_dependency = False
    
    def derive(self, ff = P('Eng (*) Fuel Flow'),
               gw = P('Gross Weight'),
               climbs = S('Climbing'),
               descends = S('Descending')
               ):
        flow = repair_mask(ff.array)
        fuel_to_burn = np.ma.array(integrate (flow/3600.0, ff.frequency,  direction='reverse'))

        to_burn_valid = []
        to_burn_all = []
        gw_valid = []
        gw_all = []
        for gw_index in gw.array.nonzero()[0]:
            # Keep all the values
            gw_all.append(gw.array.data[gw_index])
            ff_time = ((gw_index/gw.frequency)+gw.offset-ff.offset)*ff.frequency
            to_burn_all.append(value_at_index(fuel_to_burn, ff_time))
            
            # Skip values which are within Climbing or Descending phases.
            if any([is_index_within_slice(gw_index, c.slice) for c in climbs]) or \
               any([is_index_within_slice(gw_index, d.slice) for d in descends]):
                continue
            gw_valid.append(gw.array.data[gw_index])
            ff_time = ((gw_index/gw.frequency)+gw.offset-ff.offset)*ff.frequency
            to_burn_valid.append(value_at_index(fuel_to_burn, ff_time))
        
        use_valid = len(gw_valid) > 5
        use_all = len(gw_all) > 2
        offset = None
        
        if use_valid or use_all:
            if use_valid:
                corr, slope, offset = coreg(np.ma.array(gw_valid), indep_var=np.ma.array(to_burn_valid))
            elif use_all:
                corr, slope, offset = coreg(np.ma.array(gw_all), indep_var=np.ma.array(to_burn_all))
            if corr < 0.5:
                offset = gw_all[0] - to_burn_all[0]
        elif len(gw_all) == 1:
            offset = gw_all[0] - to_burn_all[0]
            
        if offset == None:
            logging.warning("Cannot smooth Gross Weight. Using the original data")
            self.frequency = ff.frequency
            self.offset = ff.offset
            self.array = align(gw, ff)
        else:
            self.array = fuel_to_burn + offset


class FlapLever(DerivedParameterNode):
    """
    Steps raw Flap angle from lever into detents.
    """
    def derive(self, flap=P('Flap Lever Position'), series=A('Series'), family=A('Family')):
        try:
            flap_steps = get_flap_map(series.value, family.value) 
        except ValueError:
            # no flaps mapping, round to nearest 5 degrees
            logging.warning("No flap settings - rounding to nearest 5")
            # round to nearest 5 degrees
            self.array = round_to_nearest(flap.array, 5.0)
        else:
            self.array = step_values(flap.array, flap_steps)
        
            
class FlapSurface(DerivedParameterNode):
    """
    Gather the recorded flap parameters and convert into a single analogue.
    """
    align_to_first_dependency = False

    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return True

    def derive(self, flap_A=P('Flap (1)'), flap_B=P('Flap (2)'),
               frame = A('Frame')):
        frame_name = frame.value if frame else None

        if frame_name in ['737-5']:
            self.array, self.frequency, self.offset = merge_two_parameters(flap_A, flap_B)
            
                    
class Flap(DerivedParameterNode):
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        return True

    def derive(self, flap=P('Flap Surface'), 
               series=A('Series'), family=A('Family'),
               flap_steps=A('Flap Selections')):
        """
        Steps raw Flap angle into detents.
        """
        try:
            flap_steps = get_flap_map(series.value, family.value) 
        except ValueError:
            # no flaps mapping, round to nearest 5 degrees
            logging.warning("No flap settings - rounding to nearest 5")
            # round to nearest 5 degrees
            self.array = round_to_nearest(flap.array, 5.0)
        else:
            self.array = step_values(flap.array, flap_steps)
        
            
class Slat(DerivedParameterNode):
    """
    Steps raw Slat angle into detents.
    """
    def derive(self, slat=P('Slat Surface'), series=A('Series'), family=A('Family')):
        try:
            slat_steps = get_slat_map(series.value, family.value) 
        except ValueError:
            # no slats mapping, round to nearest 5 degrees
            logging.warning("No slat settings - rounding to nearest 5")
            # round to nearest 5 degrees
            self.array = round_to_nearest(slat.array, 5.0)
        else:
            self.array = step_values(slat.array, slat_steps)
            
            
            
class Config(DerivedParameterNode):
    """
    Multi-state with the following mapping:
    {
        0 : '0',
        1 : '1',
        2 : '1 + F',
        3 : '2(a)',  #Q: should display be (a) or 2* or 1* ?!
        4 : '2',
        5 : '3(b)',
        6 : '3',
        7 : 'FULL',
    }
    (a) corresponds to CONF 1*
    (b) corresponds to CONF 2*
    
    Note: Does not use the Flap Lever position. This parameter reflects the
    actual config state of the aircraft rather than the intended state
    represented by the selected lever position.
    
    Note: Values that do not map directly to a required state are masked with
    the data being random (memory alocated)
    """
    @classmethod
    def can_operate(cls, available):
        return 'Flap' in available and \
               'Slat' in available and \
               'Series' in available and \
               'Family' in available
        
    def derive(self, flap=P('Flap'), slat=P('Slat'), aileron=P('Aileron'), 
               series=A('Series'), family=A('Family')):
        #TODO: manu=A('Manufacturer') - we could ensure this is only done for Airbus?
        
        mapping = get_config_map(series.value, family.value)        
        qty_param = len(mapping.itervalues().next())
        if qty_param == 3 and not aileron:
            # potential problem here!
            logging.warning("Aileron not available, so will calculate Config using only slat and flap")
            qty_param = 2
        elif qty_param == 2 and aileron:
            # only two items in values tuple
            logging.debug("Aileron available but not required for Config calculation")
            pass
        
        #TODO: Scale each parameter individually to ensure uniqueness
        # sum the required parameters
        summed = vstack_params(*(flap, slat, aileron)[:qty_param]).sum(axis=0)
        
        # create a placeholder array fully masked
        self.array = np.ma.empty_like(flap.array)
        self.array.mask=True
        for state, values in mapping.iteritems():
            s = sum(values[:qty_param])
            # unmask bits we know about
            self.array[summed == s] = state


####class GearSelectedDown(DerivedParameterNode):
####    # And here is where the nightmare starts.
####    # Sometimes recorded
####    # Sometimes interpreted from other signals
####    # There's no pattern to how this is worked out.
####    # For aircraft with a Gear Selected Down parameter let's try this...
####    def derive(self, param=P('Gear Selected Down')):
####        return NotImplemented


####class GearSelectedUp(DerivedParameterNode):
####    def derive(self, param=P('Gear Selected Up')):
####        return NotImplemented


class GroundspeedAlongTrack(DerivedParameterNode):
    # Inertial smoothing provides computation of groundspeed data when the
    # recorded groundspeed is unreliable. For example, during sliding motion
    # on a runway during deceleration. This is not good enough for long
    # period computation, but is an improvement over aircraft where the 
    # groundspeed data stops at 40kn or thereabouts.
    def derive(self, gndspd=P('Groundspeed'),
               at=P('Acceleration Along Track'),
               alt_aal=P('Altitude AAL'),
               glide = P('ILS Glideslope')):
        at_washout = first_order_washout(at.array, AT_WASHOUT_TC, gndspd.hz, 
                                         gain=GROUNDSPEED_LAG_TC*GRAVITY_METRIC)
        self.array = first_order_lag(gndspd.array*KTS_TO_MPS + at_washout,
                                     GROUNDSPEED_LAG_TC,gndspd.hz)
    
        
        """
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
        """

class HeadingContinuous(DerivedParameterNode):
    """
    For all internal computing purposes we use this parameter which does not
    jump as it passes through North. To recover the compass display, modulus
    ("%360" in Python) returns the value to display to the user.
    """
    units = 'deg'
    def derive(self, head_mag=P('Heading')):
        self.array = repair_mask(straighten_headings(head_mag.array))


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


class ILSFrequency(DerivedParameterNode):
    """
    This code is based upon the normal operation of an Instrument Landing
    System whereby the left and right receivers are tuned to the same runway
    ILS frequency. This allows independent monitoring of the approach by the
    two crew.
    
    If there is a problem with the system, users can inspect the (L) and (R)
    signals separately, although the normal use will show valid ILS data when
    both are tuned to the same frequency.
    
    """
    name = "ILS Frequency"
    align_to_first_dependency = False
    def derive(self, f1=P('ILS (L) Frequency'),f2=P('ILS (R) Frequency')):
        # Mask invalid frequencies
        f1_trim = np.ma.masked_outside(f1.array,108.10,111.95)
        f2_trim = np.ma.masked_outside(f2.array,108.10,111.95)
        # and mask where the two receivers are not matched
        self.array = np.ma.array(data = f1_trim.data,
                                 mask = np.ma.masked_not_equal(f1_trim-f2_trim,0.0).mask)
        

class ILSLocalizer(DerivedParameterNode):
    name = "ILS Localizer"
    align_to_first_dependency = False
    def derive(self, loc_1=P('ILS (L) Localizer'),loc_2=P('ILS (R) Localizer'), 
               freq=P("ILS Frequency")):
        self.array, self.frequency, self.offset = blend_two_parameters(loc_1, loc_2)
        # Would like to do this, except the frequemcies don't match
        # self.array.mask = np.ma.logical_or(self.array.mask, freq.array.mask)
               
       
class ILSGlideslope(DerivedParameterNode):
    name = "ILS Glideslope"
    align_to_first_dependency = False
    def derive(self, gs_1=P('ILS (L) Glideslope'),gs_2=P('ILS (R) Glideslope'), 
               freq=P("ILS Frequency")):
        self.array, self.frequency, self.offset = blend_two_parameters(gs_1, gs_2)
        # Would like to do this, except the frequemcies don't match
        # self.array.mask = np.ma.logical_or(self.array.mask, freq.array.mask)
       

class ILSRange(DerivedParameterNode):
    name = "ILS Range"
    """
    Range is computed from the track where available, otherwise estimated
    from available groundspeed or airspeed parameters.
    
    It is (currently) in feet from the localizer antenna.
    """
    
    ##@classmethod
    ##def can_operate(cls, available):
        ##return True
    
    def derive(self, lat=P('Latitude Straighten'),
               lon = P('Longitude Straighten'),
               glide = P('ILS Glideslope'),
               gspd = P('Groundspeed'),
               tas = P('Airspeed True'),
               alt_aal = P('Altitude AAL'),
               loc_established = S('ILS Localizer Established'),
               gs_established = S('ILS Glideslope Established'),
               precise = A('Precise Positioning'),
               app_info = A('FDR Approaches'),
               final_apps = S('Final Approach'),
               start_datetime = A('Start Datetime')
               ):
        ils_range = np.ma.zeros(len(gspd.array))
        
        for this_loc in loc_established:
            # Scan through the recorded approaches to find which matches this
            # localizer established phase.
            for approach in app_info.value:
                # line up an approach slice
                start = index_of_datetime(start_datetime.value,
                                          approach['slice_start_datetime'],
                                          self.frequency)
                stop = index_of_datetime(start_datetime.value,
                                         approach['slice_stop_datetime'],
                                         self.frequency)
                approach_slice = slice(start, stop)
                if slices_overlap(this_loc.slice, approach_slice):
                    # we've found a matching approach where the localiser was established
                    break
            else:
                logging.warning("No approach found within slice '%s'.",this_loc)
                continue

            runway = approach['runway']
            if not runway:
                logging.warning("Approach runway information not available. "
                                "No support for Airports without Runways! "
                                "Details: %s", approach)
                continue
            
            if precise.value:
                # Convert (straightened) latitude & longitude for the whole phase
                # into range from the threshold. (threshold = {})
                if 'localizer' in runway:
                    threshold = runway['localizer']
                elif 'end' in runway:
                    threshold = runway['end']
                else:
                    pass
                    # TODO: Set threshold is where the touchdown happened.
                    
                brg, ils_range[this_loc.slice] = \
                    bearings_and_distances(repair_mask(lat.array[this_loc.slice]),
                                           repair_mask(lon.array[this_loc.slice]),
                                           threshold)
                ils_range[this_loc.slice] *= METRES_TO_FEET
                continue # move onto next loc_established
                
            #-----------------------------
            #else: non-precise positioning
            
            # Use recorded groundspeed where available, otherwise estimate
            # range using true airspeed. This is because there are aircraft
            # which record ILS but not groundspeed data.
            if gspd:
                speed = np.ma.where(gspd.array.mask[this_loc.slice], \
                                    tas.array.data[this_loc.slice], \
                                    gspd.array.data[this_loc.slice]) 
            else:
                speed = tas.array.data[this_loc.slice]
                
            # Estimate range by integrating back from zero at the end of the
            # phase to high range values at the start of the phase.
            spd_repaired = repair_mask(speed)
            ils_range[this_loc.slice] = integrate(
                spd_repaired, gspd.frequency, scale=KTS_TO_FPS, direction='reverse')
            
            try:
                start_2_loc, gs_2_loc, end_2_loc, pgs_lat, pgs_lon = \
                    runway_distances(runway)  
            except KeyError:
                logging.warning("Runway did not have required information in "
                                "'%s', '%s'.", self.name, runway)
                continue
            if 'glideslope' in runway:
                # The runway has an ILS glideslope antenna
                
                for this_gs in gs_established:                    
                    if is_slice_within_slice(this_gs.slice, this_loc.slice):
                        # we'll take the first one!
                        break
                else:
                    # we didn't find a period where the glideslope was
                    # established at the same time as the localiser
                    logging.warning("No glideslope established at same time as localiser")
                    continue
                    
                # Compute best fit glidepath. The term (1-.13 x glideslope
                # deviation) caters for the aircraft deviating from the
                # planned flightpath. 1 dot low is about 0.76 deg, or 13% of
                # a 3 degree glidepath. Not precise, but adequate accuracy
                # for the small error we are correcting for here.
                corr, slope, offset = coreg(
                    alt_aal.array[this_gs.slice]* (1-0.13*glide.array[this_gs.slice]),
                    ils_range[this_gs.slice])

                # Shift the values in this approach so that the range = 0 at
                # 0ft on the projected ILS slope, then reference back to the
                # localizer antenna.                  
                datum_2_loc = gs_2_loc * METRES_TO_FEET + offset/slope
                
            else:
                # Case of an ILS approach using localizer only.
                for this_app in final_apps:
                    if is_slice_within_slice(this_app.slice, this_loc.slice):
                        # we'll take the first one!
                        break
                else:
                    # we didn't find a period where the approach was within the localiser
                    logging.warning("Approaches were not fully established with localiser")
                    continue
                    
                corr, slope, offset = coreg(
                    alt_aal.array[this_app.slice], ils_range[this_app.slice])
                
                # Touchdown point nominally 1000ft from start of runway
                datum_2_loc = (start_2_loc*METRES_TO_FEET-1000) - offset/slope
                        
                
            # Adjust all range values to relate to the localizer antenna by
            # adding the landing datum to localizer distance.
            ils_range[this_loc.slice] += datum_2_loc

        self.array = ils_range
   
    
class LatitudeSmoothed(DerivedParameterNode):
    units = 'deg'
    # Note order of longitude and latitude sets data aligned to latitude.
    def derive(self, lat = P('Latitude Straighten'),
               lon = P('Longitude Straighten'),
               loc_est = S('ILS Localizer Established'),
               ils_range = P('ILS Range'),
               ils_loc = P('ILS Localizer'),
               alt_aal = P('Altitude AAL'),
               gspd = P('Groundspeed'),
               tas = P('Airspeed True'),
               precise =A('Precise Positioning'),
               toff = S('Takeoff'),
               app_info = A('FDR Approaches'),
               toff_rwy = A('FDR Takeoff Runway'),
               start_datetime = A('Start Datetime'),
               ):
        if len(app_info.value) != len(loc_est):
            # Q: Is this still True?
            logging.warning("Cannot Smooth latitude if the number of '%s'"
                            "Sections is not equal to the number of approaches.",
                            loc_est.name)
            self.array = lat.array
            return
        
        self.array, _ = adjust_track(lon,lat,loc_est,ils_range,ils_loc,
                                        alt_aal,gspd,tas,precise,toff,
                                        app_info,toff_rwy,start_datetime)
        

class LongitudeSmoothed(DerivedParameterNode):
    units = 'deg'
    # Note order of longitude and latitude sets data aligned to longitude.
    def derive(self, lon = P('Longitude Straighten'),
               lat = P('Latitude Straighten'),
               loc_est = S('ILS Localizer Established'),
               ils_range = P('ILS Range'),
               ils_loc = P('ILS Localizer'),
               alt_aal = P('Altitude AAL'),
               gspd = P('Groundspeed'),
               tas = P('Airspeed True'),
               precise =A('Precise Positioning'),
               toff = S('Takeoff'),
               app_info = A('FDR Approaches'),
               toff_rwy = A('FDR Takeoff Runway'),
               start_datetime = A('Start Datetime'),
               ):
        if len(app_info.value) != len(loc_est) :
            # Q: Is this still True?
            logging.warning("Cannot Smooth longitude if the number of '%s'"
                            "Sections is not equal to the number of approaches.",
                            loc_est.name)
            self.array = lon.array
            return        

        _, self.array = adjust_track(lon,lat,loc_est,ils_range,ils_loc,
                                     alt_aal,gspd,tas,precise,toff,
                                     app_info,toff_rwy,start_datetime)
        
        
def adjust_track(lon,lat,loc_est,ils_range,ils_loc,alt_aal,gspd,tas,
                 precise,toff,app_info,toff_rwy,start_datetime):

    # Set up a working space.
    lat_adj = np.ma.array(data=lat.array.data,mask=True)
    lon_adj = np.ma.array(data=lon.array.data,mask=True)

    #-----------------------------------------------------------------------
    # Use synthesized track for takeoffs where necessary
    #-----------------------------------------------------------------------
    first_toff = toff.get_first()
    if not first_toff:
        raise NotImplementedError("'%s' is required for smoothing coordinates.",
                                  toff.name)
    if precise.value:
        # We allow the recorded track to be used for the takeoff unchanged.
        lat_adj[:first_toff.slice.stop] = lat.array[:first_toff.slice.stop]
        lon_adj[:first_toff.slice.stop] = lon.array[:first_toff.slice.stop]
        
    else:

        # We can improve the track using available data.
        if gspd:
            speed = gspd.array[first_toff.slice]
            freq = gspd.frequency
        else:
            speed = tas.array[first_toff.slice]
            freq = tas.frequency
            
        # Compute takeoff track from start of runway using integrated
        # groundspeed, down runway centreline to end of takeoff
        # (35ft). An initial value of 300ft puts the aircraft at a
        # reasonable position with respect to the runway start.
        rwy_dist = np.ma.array(                        
            data = integrate(speed, freq, initial_value=300, 
                             scale=KTS_TO_FPS),
            mask = gspd.array.mask[first_toff.slice])

        # The start location has been read from the database.
        # TODO: What should we do if start coordinates are not available.
        start_locn = toff_rwy.value['start']

        # Similarly the runway bearing is derived from the runway endpoints
        # (this gives better visualisation images than relying upon the
        # nominal runway heading). This is converted to a numpy masked array
        # of the length required to cover the takeoff phase. (This is a bit
        # clumsy, because there is no np.ma.ones_like method).
        hdg = runway_heading(toff_rwy.value)
        rwy_brg = np.ma.array(data = np.ones_like(speed)*hdg, mask = False)
        
        # And finally the track down the runway centreline is
        # converted to latitude and longitude.
        lat_adj[toff[0].slice], lon_adj[first_toff.slice] = \
            latitudes_and_longitudes(rwy_brg, 
                                     rwy_dist/METRES_TO_FEET, 
                                     start_locn)                    
    
    #-----------------------------------------------------------------------
    # Use ILS track for approach and landings in all localizer approches
    #-----------------------------------------------------------------------
    
    for this_loc in loc_est:    
        # Join with ILS bearings (inherently from the localizer) and
        # revert the ILS track from range and bearing to lat & long
        # coordinates.
        # Scan through the recorded approaches to find which matches this
        # localizer established phase.
        for approach in app_info.value:
            # line up an approach slice
            start = index_of_datetime(start_datetime.value,
                                      approach['slice_start_datetime'],
                                      lon.frequency)
            stop = index_of_datetime(start_datetime.value,
                                     approach['slice_stop_datetime'],
                                     lon.frequency)
            approach_slice = slice(start, stop)
            if slices_overlap(this_loc.slice, approach_slice):
                # we've found a matching approach where the localiser was established
                break
        else:
            logging.warning("No approach found within slice '%s'.",this_loc)
            continue
        
        runway = approach['runway']
        if not runway:
            logging.error("Approach runway information not available.")
            raise NotImplementedError(
                "No support for Airports without Runways! Details: %s" % approach)    
        
        if 'localizer' in runway:
            reference = runway['localizer']
            
            if 'beam_width' in reference:
                # Compute the localizer scale factor (degrees per dot)
                # Half the beam width is 2 dots full scale
                scale = (reference['beam_width']/2.0) / 2.0
            else:
                # Normal scaling of a localizer gives 700ft width at the threshold
                scale = np.degrees(np.arctan2(700/2, runway_length(runway)*METRES_TO_FEET)) / 2.0
                
            # Adjust the ils data to be degrees from the reference point.
            bearings = ils_loc.array[this_loc.slice] * scale + \
                runway_heading(runway)+180
            
            # Adjust distance units
            distances = ils_range.array[this_loc.slice] / METRES_TO_FEET
            
            # Tweek the localizer position to be on the start:end centreline
            localizer_on_cl = ils_localizer_align(runway)
            
            # At last, the conversion of ILS localizer data to latitude and longitude
            lat_adj[this_loc.slice], lon_adj[this_loc.slice] = \
                latitudes_and_longitudes(bearings, distances, localizer_on_cl)

    # --- Merge Tracks and return ---
    return track_linking(lat.array, lat_adj), track_linking(lon.array, lon_adj)

          

class Mach(DerivedParameterNode):
    def derive(self, cas = P('Airspeed'), alt = P('Altitude STD')):
        dp = cas2dp(cas.array)
        p = alt2press(alt.array)
        self.array = dp_over_p2mach(dp/p)
       

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
        # List the minimum required parameters.
        return 'Altitude STD' in available
    
    def derive(self, 
               az = P('Acceleration Vertical'),
               alt_std = P('Altitude STD'),
               alt_rad = P('Altitude Radio'),
               speed=P('Airspeed')):

        def inertial_rate_of_climb(alt_std_repair, frequency, alt_rad_repair, az_repair):
            # Uses the complementary smoothing approach
            
            # This is the accelerometer washout term, with considerable gain.
            # The initialisation "initial_value=az.array[clump][0]" is very
            # important, as without this the function produces huge spikes at
            # each start of a data period.
            az_washout = first_order_washout (az_repair, 
                                              AZ_WASHOUT_TC, frequency, 
                                              gain=GRAVITY_IMPERIAL,
                                              initial_value=az_repair[0])
            inertial_roc = first_order_lag (az_washout, 
                                            RATE_OF_CLIMB_LAG_TC, 
                                            frequency, 
                                            gain=RATE_OF_CLIMB_LAG_TC)
    
            # Both sources of altitude data are differentiated before
            # merging, as we mix height rate values to minimise the effect of
            # changeover of sources.
            roc_alt_std = first_order_washout(alt_std_repair,
                                              RATE_OF_CLIMB_LAG_TC, frequency,
                                              gain=1/RATE_OF_CLIMB_LAG_TC)
            roc_alt_rad = first_order_washout(alt_rad_repair,
                                              RATE_OF_CLIMB_LAG_TC, frequency,
                                              gain=1/RATE_OF_CLIMB_LAG_TC)
                    
            # Use pressure altitude rate above 100ft and radio altitude rate
            # below 50ft with progressive changeover across that range.
            # up to 50 ft radio 0 < std_rad_ratio < 1 over 100ft radio
            std_rad_ratio = np.maximum(np.minimum((alt_rad_repair-50.0)/50.0,
                                                  1),0)
            roc_altitude = roc_alt_std*std_rad_ratio +\
                roc_alt_rad*(1.0-std_rad_ratio)
            
            return (roc_altitude + inertial_roc) * 60.0

        if az and alt_rad:
            # Make space for the answers
            self.array = np.ma.masked_all_like(alt_std.array)
            
            # Fix minor dropouts
            az_repair = repair_mask(az.array)
            alt_rad_repair = repair_mask(alt_rad.array, frequency=alt_rad.frequency)
            alt_std_repair = repair_mask(alt_std.array, frequency=alt_std.frequency)
            
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
                
                 self.array[clump] = inertial_rate_of_climb(
                     alt_std_repair[clump], az.frequency,
                     alt_rad_repair[clump], az_repair[clump])
            
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
    def derive(self, alt_std = P('Altitude STD')):
        # This uses a scaled hysteresis parameter. See settings for more detail.
        threshold = HYSTERESIS_FPROC * max(1, rms_noise(alt_std.array))  
        # The max(1, prevents =0 case when testing with artificial data.
        self.array = hysteresis(rate_of_change(alt_std,3)*60,threshold)


class Relief(DerivedParameterNode):
    # also known as Terrain
    
    # Quickly written without tests as I'm really editing out the old dependencies statements :-(
    def derive(self, alt_aal = P('Altitude AAL'),
               alt_rad = P('Altitude Radio')):
        self.array = alt_aal.array - alt_rad.array


class Rudder(DerivedParameterNode):
    def derive(self, rudd=P('RUDDER POSN')):
        self.array, self.offset, self.frequency = rudd.array, rudd.offset, rudd.frequency

####class Speedbrake(DerivedParameterNode):
####    def derive(self, param=P('Speedbrake')):
####        # There will be a recorded parameter, but varying types of correction will 
####        # need to be applied according to the aircraft type and data frame.
####        return NotImplemented


class CoordinatesStraighten(object):
    '''
    Superclass for LatitudeStraighten and LongitudeStraighten.
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
        

class LongitudeStraighten(DerivedParameterNode, CoordinatesStraighten):
    def derive(self,
               lon=P('Longitude'),
               lat=P('Latitude')):
        self.array = self._smooth_coordinates(lon, lat)

    
class LatitudeStraighten(DerivedParameterNode, CoordinatesStraighten):
    def derive(self, 
               lat=P('Latitude'), 
               lon=P('Longitude')):
        self.array = self._smooth_coordinates(lat, lon)


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
        # TODO: What should half_width argument to rate_of_change be?
        self.array = rate_of_change(pitch, 1)


class ThrottleLever(DerivedParameterNode):
    def derive(self,
               tla1=P('THR LEVER ANGLE-LEFT'), 
               tla2=P('THR LEVER ANGLE-RIGHT')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(tla1, tla2)


class Aileron(DerivedParameterNode):
    '''
    '''
    # TODO: TEST
    name = 'Aileron'

    @classmethod
    def can_operate(cls, available):
       a = set(['Aileron (L)', 'Aileron (R)'])
       b = set(['Aileron (L) Inboard', 'Aileron (R) Inboard', 'Aileron (L) Outboard', 'Aileron (R) Outboard'])
       x = set(available)
       return not (a - x) or not (b - x)

    def derive(self,
               al=P('Aileron (L)'),
               ar=P('Aileron (R)'),
               ali=P('Aileron (L) Inboard'),
               ari=P('Aileron (R) Inboard'),
               alo=P('Aileron (L) Outboard'),
               aro=P('Aileron (R) Outboard')):
        return NotImplemented


class AileronTrim(DerivedParameterNode): # RollTrim
    '''
    '''
    # TODO: TEST
    name = 'Aileron Trim' # Roll Trim

    def derive(self,
               atl=P('Aileron Trim (L)'),
               atr=P('Aileron Trim (R)')):
        return NotImplemented


class Elevator(DerivedParameterNode):
    '''
    '''
    # TODO: TEST
    name = 'Elevator'

    def derive(self,
               el=P('Elevator (L)'),
               er=P('Elevator (R)')):
        return NotImplemented


class ElevatorTrim(DerivedParameterNode): # PitchTrim
    '''
    '''
    # TODO: TEST
    name = 'Elevator Trim' # Pitch Trim

    def derive(self,
               etl=P('Elevator Trim (L)'),
               etr=P('Elevator Trim (R)')):
        return NotImplemented


class Spoiler(DerivedParameterNode):
    '''
    '''
    # TODO: TEST
    name = 'Spoiler'

    def derive(self,
               s01=P('Spoiler (1)'),
               s02=P('Spoiler (2)'),
               s03=P('Spoiler (3)'),
               s04=P('Spoiler (4)'),
               s05=P('Spoiler (5)'),
               s06=P('Spoiler (6)'),
               s07=P('Spoiler (7)'),
               s08=P('Spoiler (8)'),
               s09=P('Spoiler (9)'),
               s10=P('Spoiler (10)'),
               s11=P('Spoiler (11)'),
               s12=P('Spoiler (12)')):
        return NotImplemented


class Speedbrake(DerivedParameterNode):
    '''
    '''
    # TODO: TEST
    name = 'Speedbrake'

    def derive(self,
               s01=P('Spoiler (1)'),
               s02=P('Spoiler (2)'),
               s03=P('Spoiler (3)'),
               s04=P('Spoiler (4)'),
               s05=P('Spoiler (5)'),
               s06=P('Spoiler (6)'),
               s07=P('Spoiler (7)'),
               s08=P('Spoiler (8)'),
               s09=P('Spoiler (9)'),
               s10=P('Spoiler (10)'),
               s11=P('Spoiler (11)'),
               s12=P('Spoiler (12)')):
        return NotImplemented

class StickShaker(DerivedParameterNode):
    def derive(self, shake=P('STICK SHAKER-LEFT')):
        self.array, self.frequency, self.offset = \
            shake.array, shake.frequency, shake.offset
               

"""
class ILSTestOutput(DerivedParameterNode):
    name = "ILS TEST OUTPUT"
    
    def derive(self, lat=P('Latitude'),
               lon = P('Longitude'),
               glide = P('ILS Glideslope'),
               ils_loc = P('ILS Localizer'),
               alt_aal = P('Altitude AAL'),
               fast=S('Fast')):

        #-------------------------------------------------------------------
        # TEST OUTPUT TO CSV FILE FOR DEBUGGING ONLY
        # TODO: REMOVE THIS SECTION BEFORE RELEASE
        #-------------------------------------------------------------------
        ##import csv
        ##spam = csv.writer(open('tomato.csv', 'wb'))
        ##spam.writerow(['ILS Localizer',
                       ##'ILS Glideslope',
                       ##'Altitude AAL',
                       ##'Altitude Radio',
                       ##'Heading', 'Bearing', 'Distance',
                       ##'Longitude',
                       ##'Latitude',
                       ##'Longitude Return',
                       ##'Latitude Return'])
        ###scope = ap.get_last().slice  # Only the last approach is interesting.
        for speedy in fast:
            scope = slice(int(speedy.slice.stop-400),int(speedy.slice.stop))
            #Option to track back to localiser intercept
            capture = index_at_value(ils_loc.array,4.0,slice(scope.start,0,-1))
            newslice = slice(capture, int(scope.stop)+20)
            if lat.array[scope][-1] > 62:
                # Trondheim = TRD
                lzr_loc = {'latitude': 63.45763, 'longitude': 10.90043}
                lzr_hdg = 89-180
            elif lon.array[scope][-1] < 7:
                # Bergen = BGO
                lzr_loc = {'latitude': 60.30112, 'longitude': 5.21556}
                lzr_hdg = 173-180
            else:
                # Oslo = OSL
                lzr_loc = {'latitude': 60.2134, 'longitude': 11.08986}
                lzr_hdg = 196-180
                
            brg,dist=bearings_and_distances(lat.array[newslice], lon.array[newslice], lzr_loc)
            
            ##lat_trk,lon_trk=latitudes_and_longitudes((ils_loc.array[newslice]-lzr_hdg)/180*3.14159,dist, rwy.value['localizer'])
            
            #outfile=open('C:/POLARIS Development/AnalysisEngine/tests/test.npy', 'w')
            np.savez('C:/POLARIS Development/AnalysisEngine/tests/test',
                     alt_aal=alt_aal.array.data[newslice], dist=dist.data, glide=glide.array.data[newslice])
            #outfile.close()
            
            #outfile.seek(0)
            #npzfile = np.load(outfile)
            #npzfile.files
            #['y', 'x']
            #>>> npzfile['x']
            #array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])            
        
        ##for showme in range(newslice.start, newslice.stop):
        ###for showme in range(0, len(ils_loc.array)):
            ##spam.writerow([ils_loc.array[showme],
                           ##glide.array[showme],
                           ##alt_aal.array[showme],
                           ##alt_rad.array[showme],
                           ##hdg.array[showme]%360.0,
                           ##brg[showme-newslice.start]*180/3.14159,
                           ##dist[showme-newslice.start]*1000/25.4/12,
                           ##lon.array[showme],
                           ##lat.array[showme],
                           ##lon_trk[showme-newslice.start],
                           ##lat_trk[showme-newslice.start]
                           ##])
    ##self.array = np.ma.arange(1000) # TODO: Remove.
    #-------------------------------------------------------------------
    # TEST OUTPUT TO CSV FILE FOR DEBUGGING ONLY
    # TODO: REMOVE THIS SECTION BEFORE RELEASE
    #-------------------------------------------------------------------
"""    
