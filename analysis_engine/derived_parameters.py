# -*- coding: utf-8 -*-

import numpy as np
import geomag

from math import ceil, radians
from scipy.interpolate import interp1d

from analysis_engine.exceptions import DataFrameError

from flightdatautilities.model_information import (get_aileron_map,
                                                   get_conf_map,
                                                   get_flap_map,
                                                   get_slat_map)
from flightdatautilities.velocity_speed import get_vspeed_map
from hdfaccess.parameter import MappedArray

from analysis_engine.node import (
    A, App, DerivedParameterNode, MultistateDerivedParameterNode, KPV, KTI, M,
    P, S)
from analysis_engine.library import (actuator_mismatch,
                                     air_track,
                                     align,
                                     all_of,
                                     any_of,
                                     alt2press,
                                     alt2sat,
                                     bearing_and_distance,
                                     bearings_and_distances,
                                     blend_parameters,
                                     blend_two_parameters,
                                     cas2dp,
                                     coreg,
                                     cycle_finder,
                                     datetime_of_index,
                                     dp2tas,
                                     dp_over_p2mach,
                                     filter_vor_ils_frequencies,
                                     first_valid_sample,
                                     first_order_lag,
                                     first_order_washout,
                                     ground_track,
                                     ground_track_precise,
                                     hysteresis,
                                     index_at_value,
                                     integrate,
                                     ils_localizer_align,
                                     index_closest_value,
                                     interpolate,
                                     is_day,
                                     is_index_within_slice,
                                     last_valid_sample,
                                     latitudes_and_longitudes,
                                     localizer_scale,
                                     machtat2sat,
                                     mask_inside_slices,
                                     mask_outside_slices,
                                     max_value,
                                     merge_masks,
                                     merge_two_parameters,
                                     moving_average,
                                     np_ma_ones_like,
                                     np_ma_masked_zeros_like,
                                     np_ma_zeros_like,
                                     offset_select,
                                     peak_curvature,
                                     rate_of_change,
                                     repair_mask,
                                     rms_noise,
                                     round_to_nearest,
                                     runway_deviation,
                                     runway_distances,
                                     runway_heading,
                                     runway_length,
                                     runway_snap_dict,
                                     shift_slice,
                                     slices_between,
                                     slices_from_to,
                                     slices_not,
                                     slices_or,
                                     smooth_track,
                                     step_values,
                                     straighten_altitudes,
                                     straighten_headings,
                                     second_window,
                                     track_linking,
                                     value_at_index,
                                     vstack_params,
                                     vstack_params_where_state)

from settings import (AZ_WASHOUT_TC,
                      FEET_PER_NM,
                      HYSTERESIS_FPIAS,
                      HYSTERESIS_FPROC,
                      GRAVITY_IMPERIAL,
                      KTS_TO_FPS,
                      KTS_TO_MPS,
                      METRES_TO_FEET,
                      METRES_TO_NM,
                      VERTICAL_SPEED_LAG_TC)

# There is no numpy masked array function for radians, so we just multiply thus:
deg2rad = radians(1.0)

class AccelerationLateralOffsetRemoved(DerivedParameterNode):
    """
    This process attempts to remove datum errors in the lateral accelerometer.
    """
    @classmethod
    def can_operate(cls, available):
        return 'Acceleration Lateral' in available

    units = 'g'

    def derive(self, acc=P('Acceleration Lateral'),
               offset=KPV('Acceleration Lateral Offset')):
        if offset:
            self.array = acc.array - offset[0].value
        else:
            self.array = acc.array


class AccelerationLongitudinalOffsetRemoved(DerivedParameterNode):
    """
    This process attempts to remove datum errors in the longitudinal accelerometer.
    """
    @classmethod
    def can_operate(cls, available):
        return 'Acceleration Longitudinal' in available

    units = 'g'

    def derive(self, acc=P('Acceleration Longitudinal'),
               offset=KPV('Acceleration Longitudinal Offset')):
        if offset:
            self.array = acc.array - offset[0].value
        else:
            self.array = acc.array


class AccelerationNormalOffsetRemoved(DerivedParameterNode):
    """
    This process attempts to remove datum errors in the normal accelerometer.
    """
    @classmethod
    def can_operate(cls, available):
        return 'Acceleration Normal' in available

    units = 'g'

    def derive(self, acc=P('Acceleration Normal'),
               offset=KPV('Acceleration Normal Offset')):
        if offset:
            self.array = acc.array - offset[0].value + 1.0 # 1.0 to reset datum.
        else:
            self.array = acc.array


class AccelerationVertical(DerivedParameterNode):
    """
    Resolution of three accelerations to compute the vertical
    acceleration (perpendicular to the earth surface). Result is in g,
    retaining the 1.0 datum and positive upwards.
    """

    units = 'g'

    def derive(self, acc_norm=P('Acceleration Normal Offset Removed'),
               acc_lat=P('Acceleration Lateral Offset Removed'),
               acc_long=P('Acceleration Longitudinal'),
               pitch=P('Pitch'), roll=P('Roll')):
        # FIXME: FloatingPointError: underflow encountered in multiply
        pitch_rad = pitch.array * deg2rad
        roll_rad = roll.array * deg2rad
        resolved_in_roll = acc_norm.array * np.ma.cos(roll_rad)\
            - acc_lat.array * np.ma.sin(roll_rad)
        self.array = resolved_in_roll * np.ma.cos(pitch_rad) \
                     + acc_long.array * np.ma.sin(pitch_rad)


class AccelerationForwards(DerivedParameterNode):
    """
    Resolution of three body axis accelerations to compute the forward
    acceleration, that is, in the direction of the aircraft centreline
    when projected onto the earth's surface.

    Forwards = +ve, Constant sensor errors not washed out.
    """

    units = 'g'

    def derive(self, acc_norm=P('Acceleration Normal Offset Removed'),
               acc_long=P('Acceleration Longitudinal'),
               pitch=P('Pitch')):
        pitch_rad = pitch.array * deg2rad
        self.array = acc_long.array * np.ma.cos(pitch_rad)\
                     - acc_norm.array * np.ma.sin(pitch_rad)


class AccelerationAcrossTrack(DerivedParameterNode):
    """
    The forward and sideways ground-referenced accelerations are resolved
    into along track and across track coordinates in preparation for
    groundspeed computations.
    """

    units = 'g'

    def derive(self, acc_fwd=P('Acceleration Forwards'),
               acc_side=P('Acceleration Sideways'),
               drift=P('Drift')):
        drift_rad = drift.array*deg2rad
        self.array = acc_side.array * np.ma.cos(drift_rad)\
            - acc_fwd.array * np.ma.sin(drift_rad)


class AccelerationAlongTrack(DerivedParameterNode):
    """
    The forward and sideways ground-referenced accelerations are resolved
    into along track and across track coordinates in preparation for
    groundspeed computations.
    """

    units = 'g'

    def derive(self, acc_fwd=P('Acceleration Forwards'),
               acc_side=P('Acceleration Sideways'),
               drift=P('Drift')):
        drift_rad = drift.array*deg2rad
        self.array = acc_fwd.array * np.ma.cos(drift_rad)\
                     + acc_side.array * np.ma.sin(drift_rad)


class AccelerationSideways(DerivedParameterNode):
    """
    Resolution of three body axis accelerations to compute the lateral
    acceleration, that is, in the direction perpendicular to the aircraft
    centreline when projected onto the earth's surface. Right = +ve.
    """

    units = 'g'

    def derive(self, acc_norm=P('Acceleration Normal Offset Removed'),
               acc_lat=P('Acceleration Lateral Offset Removed'),
               acc_long=P('Acceleration Longitudinal'),
               pitch=P('Pitch'), roll=P('Roll')):
        pitch_rad = pitch.array * deg2rad
        roll_rad = roll.array * deg2rad
        # Simple Numpy algorithm working on masked arrays
        resolved_in_pitch = (acc_long.array * np.ma.sin(pitch_rad)
                             + acc_norm.array * np.ma.cos(pitch_rad))
        self.array = (resolved_in_pitch * np.ma.sin(roll_rad)
                      + acc_lat.array * np.ma.cos(roll_rad))


class AirspeedForFlightPhases(DerivedParameterNode):

    units = 'kts'

    def derive(self, airspeed=P('Airspeed')):
        self.array = hysteresis(
            repair_mask(airspeed.array, repair_duration=None), HYSTERESIS_FPIAS)


################################################################################
# Airspeed Minus V2 (Airspeed relative to V2 or a fixed value.)


# TODO: Ensure that this derived parameter supports fixed values.
class AirspeedMinusV2(DerivedParameterNode):
    '''
    Airspeed on takeoff relative to:

    - V2    -- Airbus, Boeing, or any other aircraft that has V2.
    - Fixed -- Prop aircraft, or as required.

    A fixed value will most likely be zero making this relative airspeed
    derived parameter the same as the original absolute airspeed parameter.
    '''
    @classmethod
    def can_operate(cls, available):
        return ('Airspeed' in available and ('V2' in available or 'V2 Lookup' in available))

    units = 'kts'

    def derive(self, airspeed=P('Airspeed'),
               v2_recorded=P('V2'),
               v2_lookup=P('V2 Lookup')):
        '''
        Where V2 is recorded, a low permitted rate of change of 1.0 kt/sec
        (specified in the Parameter Operating Limit section of the POLARIS
        database) forces all false data to be masked, leaving only the
        required valid data. By repairing the mask with duration = None, the
        valid data is extended. For example, 737-3C data only records V2 on
        the runway and it needs to be extended to permit V-V2 KPVs to be
        recorded during the climbout.
        '''
        
        if v2_recorded:
            v2 = v2_recorded
        else:
            v2 = v2_lookup
        # If the data starts in mid-flight, there may be no valid V2 values.
        if np.ma.count(v2.array):
            repaired_v2 = repair_mask(v2.array,
                                      copy=True,
                                      repair_duration=None,
                                      extrapolate=True)
            self.array = airspeed.array - repaired_v2
        else:
            self.array = np_ma_zeros_like(airspeed.array)

            #param.invalid = 1
            #param.array.mask = True
            #hdf.set_param(param, save_data=False, save_mask=True)
            #logger.info("Marked param '%s' as invalid as ptp %.2f "\
                        #"did not exceed minimum change %.2f",
                        #ptp, min_change)


class AirspeedMinusV2For3Sec(DerivedParameterNode):
    '''
    Airspeed on takeoff relative to V2 over a 3 second window.

    See the derived parameter 'Airspeed Minus V2'.
    '''

    units = 'kts'
    
    align_frequency = 2
    align_offset = 0

    def derive(self, spd_v2=P('Airspeed Minus V2')):
        '''
        '''
        self.array = second_window(spd_v2.array, self.frequency, 3)
        #self.array = clip(spd_v2.array, 3.0, spd_v2.frequency)


################################################################################
# Airspeed Relative (Airspeed relative to Vapp, Vref or a fixed value.)


class AirspeedReference(DerivedParameterNode):
    '''
    Airspeed on approach will use recorded value if present. If no recorded
    value AFR values will be used.

    Achieved flight records without a recorded value will be repeated
    thoughout the approach sections of the flight.

    - Vapp  -- Airbus
    - Vref  -- Boeing
    - Fixed -- Prop aircraft, or as required.

    A fixed value will most likely be zero making this relative airspeed
    derived parameter the same as the original absolute airspeed parameter.
    '''

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        vapp = 'Vapp' in available
        vref = 'Vref' in available
        afr = 'Airspeed' in available and any_of(['AFR Vapp', 'AFR Vref'], available)
        return vapp or vref or afr

    def derive(self,
               air_spd=P('Airspeed'),
               vapp=P('Vapp'),
               vref=P('Vref'),
               afr_vapp=A('AFR Vapp'),
               afr_vref=A('AFR Vref'),
               approaches=S('Approach And Landing')):

        if vapp:
            # Use recorded Vapp parameter:
            self.array = vapp.array
        elif vref:
            # Use recorded Vref parameter:
            self.array = vref.array
        else:
            # Use provided Vapp/Vref from achieved flight record:
            afr_vspeed = afr_vapp or afr_vref
            self.array = np.ma.zeros(len(air_spd.array), np.double)
            self.array.mask = True
            for approach in approaches:
                self.array[approach.slice] = afr_vspeed.value


class AirspeedReferenceLookup(DerivedParameterNode):
    '''
    Airspeed on approach lookup based on weight and Flap (Surface detents) at
    landing will be used.

    Flap is used as first dependant to avoid interpolation of Flap detents when
    Flap is recorded at a lower frequency than Airspeed.

    if approach leads to touchdown use max flap/conf recorded in approach phase.
    if approach does not lead to touchdown use max flaps recorded in approach phase
    if flap/conf not in lookup table use max flaps setting
    '''

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        x = set(available)
        base = ['Airspeed', 'Series', 'Family', 'Approach And Landing',
                'Touchdown']
        weight = base + ['Gross Weight Smoothed']
        airbus = set(weight + ['Configuration']).issubset(x)
        boeing = set(weight + ['Flap']).issubset(x)
        propeller = set(base + ['Eng (*) Np Avg']).issubset(x)
        # FIXME: Replace the flaky logic for small propeller aircraft which do
        #        not record gross weight, cannot provide achieved flight
        #        records and will be using a fixed value for processing.
        return airbus or boeing  # or propeller

    def derive(self,
               flap=M('Flap'),
               conf=P('Configuration'),
               air_spd=P('Airspeed'),
               gw=P('Gross Weight Smoothed'),
               approaches=S('Approach And Landing'),
               touchdowns=KTI('Touchdown'),
               series=A('Series'),
               family=A('Family'),
               engine=A('Engine Series'),
               engine_type=A('Engine Type'),
               spd_ref=P('Airspeed Reference'),
               eng_np=P('Eng (*) Np Avg')):
        '''
        Raises KeyError if no entries for Family/Series in vspeed lookup map.
        '''

        self.array = np_ma_masked_zeros_like(air_spd.array)

        x = map(lambda x: x.value if x else None, (series, family, engine, engine_type))
        try:
            vspeed_class = get_vspeed_map(*x)
        except KeyError as err:
            if spd_ref:
                self.info("Error in '%s': %s", self.name, err)
            else:
                self.warning("Error in '%s': %s", self.name, err)
            return

        if gw is not None:  # and you must have eng_np
            try:
                # Allow up to 2 superframe values to be repaired:
                # (64 * 2 = 128 + a bit)
                repaired_gw = repair_mask(gw.array, repair_duration=130,
                                          copy=True, extrapolate=True)
            except:
                self.warning("'Airspeed Reference' will be fully masked "
                    "because 'Gross Weight Smoothed' array could not be "
                    "repaired.")
                return

        setting_param = conf or flap # check Conf as is dependant on Flap
        vspeed_table = vspeed_class()
        for approach in approaches:
            _slice = approach.slice
            index, setting = max_value(setting_param.array, _slice)
            # Allow no gross weight for aircraft which use a fixed vspeed
            weight = repaired_gw[index] if gw is not None else None

            if not is_index_within_slice(touchdowns.get_last().index, _slice) \
                and setting not in vspeed_table.vref_settings:
                # Not the final landing and max setting not in vspeed table,
                # so use the maximum setting possible as a reference.
                if setting_param.name == 'Flap':
                    max_setting = max(get_flap_map(series.value, family.value))
                else:
                    max_setting = max(get_conf_map(series.value, family.value).keys())
                self.info("No touchdown in this approach and maximum "
                          "%s '%s' not in lookup table. Using max "
                          "possible setting '%s' as reference",
                          setting_param.name, setting, max_setting)
                setting = max_setting
            else:
                # We either touched down, so use the touchdown flap/conf
                # setting or we had reached a maximum flap setting during the
                # approach which in the vref table. Continue to establish Vref.
                pass

            try:
                vspeed = vspeed_table.vref(setting, weight)
            except  (KeyError, ValueError) as err:
                if spd_ref:
                    self.info("Error in '%s': %s", self.name, err)
                else:
                    self.warning("Error in '%s': %s", self.name, err)
                # Where the aircraft takes off with flap settings outside the
                # documented vref range, we need the program to continue without
                # raising an exception, so that the incorrect flap at landing
                # can be detected.
            else:
                if vspeed is not None:
                    self.array[_slice] = vspeed


class AirspeedRelative(DerivedParameterNode):
    '''
    Airspeed minus Vref/Vapp if recorded or supplied by AFR records. Falls
    back to lookup tables if these do not exist.
    '''

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        return 'Airspeed' in available and \
               any_of(('Airspeed Reference','Airspeed Reference Lookup'), available)

    def derive(self, airspeed=P('Airspeed'),
               vref_recorded=P('Airspeed Reference'),
               vref_lookup=P('Airspeed Reference Lookup')):

        if vref_recorded:
            vref = vref_recorded
        else:
            vref = vref_lookup

        self.array = airspeed.array - vref.array


class AirspeedRelativeFor3Sec(DerivedParameterNode):
    '''
    Airspeed on approach relative to Vapp/Vref over a 3 second window.

    See the derived parameter 'Airspeed Relative'.
    '''

    units = 'kts'
    align_frequency = 2
    align_offset = 0

    def derive(self, spd_vref=P('Airspeed Relative')):
        '''
        '''
        self.array = second_window(spd_vref.array, self.frequency, 3)


################################################################################


class AirspeedTrue(DerivedParameterNode):
    """
    True airspeed is computed from the recorded airspeed and pressure
    altitude. We assume that the recorded airspeed is indicated or computed,
    and that the pressure altitude is on standard (1013mB = 29.92 inHg).

    There are a few aircraft still operating which do not record the air
    temperature, so only these two parameters are required for the algorithm
    to run.

    Where air temperature is available, we accept Static Air Temperature
    (SAT) and include this accordingly. If TAT is recorded, it will have
    already been converted by the SAT derive function.

    True airspeed is also extended to the ends of the takeoff and landing
    run, in particular so that we can estimate the minimum airspeed at which
    thrust reversers are used.

    -------------------------------------------------------------------------
    Thanks are due to Kevin Horton of Ottawa for permission to derive the
    code here from his AeroCalc library.
    -------------------------------------------------------------------------
    """

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        return 'Airspeed' in available and 'Altitude STD' in available

    def derive(self, cas_p=P('Airspeed'), alt_std_p=P('Altitude STD'),
               sat_p=P('SAT'), toffs=S('Takeoff'), lands=S('Landing'),
               gspd=P('Groundspeed'), acc_fwd=P('Acceleration Forwards')):

        cas = cas_p.array
        alt_std = alt_std_p.array
        dp = cas2dp(cas)
        if sat_p:
            sat = sat_p.array
            tas = dp2tas(dp, alt_std, sat)
            combined_mask= np.logical_or(
                np.logical_or(np.ma.getmaskarray(cas_p.array),
                              np.ma.getmaskarray(alt_std_p.array)),
                np.ma.getmaskarray(sat_p.array))
        else:
            sat = alt2sat(alt_std)
            tas = dp2tas(dp, alt_std, sat)
            combined_mask= np.logical_or(cas_p.array.mask,alt_std_p.array.mask)

        tas_from_airspeed = np.ma.masked_less(
            np.ma.array(data=tas, mask=combined_mask), 50)
        tas_valids = np.ma.clump_unmasked(tas_from_airspeed)

        if all([gspd, toffs, lands]):
            # Now see if we can extend this during the takeoff phase, using
            # either recorded groundspeed or failing that integrating
            # acceleration:
            for toff in toffs:
                for tas_valid in tas_valids:
                    tix = tas_valid.start
                    if is_index_within_slice(tix, toff.slice):
                        tas_0 = tas_from_airspeed[tix]
                        wind = tas_0 - gspd.array[tix]
                        scope = slice(toff.slice.start, tix)
                        if gspd:
                            tas_from_airspeed[scope] = gspd.array[scope] + wind
                        else:
                            tas_from_airspeed[scope] = \
                                integrate(acc_fwd.array[scope],
                                          acc_fwd.frequency,
                                          initial_value=tas_0,
                                          scale=GRAVITY_IMPERIAL / KTS_TO_FPS,
                                          direction='backwards')

            # Then see if we can do the same for the landing phase:
            for land in lands:
                for tas_valid in tas_valids:
                    tix = tas_valid.stop - 1
                    if is_index_within_slice(tix, land.slice):
                        tas_0 = tas_from_airspeed[tix]
                        wind = tas_0 - gspd.array[tix]
                        scope = slice(tix + 1, land.slice.stop)
                        if gspd:
                            tas_from_airspeed[scope] = gspd.array[scope] + wind
                        else:
                            tas_from_airspeed[scope] = \
                                integrate(acc_fwd.array[scope],
                                          acc_fwd.frequency,
                                          initial_value=tas_0,
                                          scale=GRAVITY_IMPERIAL / KTS_TO_FPS)

        self.array = tas_from_airspeed


class AltitudeAAL(DerivedParameterNode):
    """
    This is the main altitude measure used during flight analysis.

    Where radio altimeter data is available, this is used for altitudes up to
    100ft and thereafter the pressure altitude signal is used. The two are
    "joined" together at the sample above 100ft in the climb or descent as
    appropriate.

    If no radio altitude signal is available, the simple measure based on
    pressure altitude only is used, which provides workable solutions except
    that the point of takeoff and landing may be inaccurate.

    This parameter includes a rejection of bounced landings of less than 35ft
    height.
    """
    name = "Altitude AAL"
    units = 'ft'
    align_frequency = 2 
    align_offset = 0

    @classmethod
    def can_operate(cls, available):
        return 'Altitude STD Smoothed' in available and 'Fast' in available

    def compute_aal(self, mode, alt_std, low_hb, high_gnd, alt_rad=None):

        alt_result = np_ma_zeros_like(alt_std)

        def shift_alt_std():
            '''
            Return Altitude STD Smoothed shifted relative to 0 for cases where we do not
            have a reliable Altitude Radio.
            '''
            try:
                # Test case => NAX_8_LN-NOE_20120109063858_02_L3UQAR___dev__sdb.002.hdf5
                # Look over the first 500ft of climb (or less if the data doesn't get that high).
                to = index_at_value(alt_std, min(alt_std[0]+500, np.ma.max(alt_std)))
                # Seek the point where the altitude first curves upwards.
                idx = int(peak_curvature(repair_mask(alt_std[:to]),
                                         curve_sense='Concave',
                                         gap = 7,
                                         ttp = 10))
                # The liftoff most probably arose in the preceding 10
                # seconds. Allow 3 seconds afterwards for luck.
                rotate = slice(max(idx-10*self.frequency,0),
                               idx+3*self.frequency)
                # Draw a straight line across this period with a ruler.
                p,m,c = coreg(alt_std[rotate])
                ruler = np.ma.arange(rotate.stop-rotate.start)*m+c
                # Measure how far the altitude is below the ruler.
                delta = alt_std[rotate] - ruler
                # The liftoff occurs where the gap is biggest because this is
                # where the wing lift has caused the local pressure to
                # increase, hence the altitude appears to decrease.
                pit = alt_std[np.ma.argmin(delta)+rotate.start]
                
                
                '''
                # Quick visual check of the operation of the takeoff point detection.
                import matplotlib.pyplot as plt
                plt.plot(alt_std[:to])
                xnew = np.linspace(rotate.start,rotate.stop,num=2)
                ynew = (xnew-rotate.start)*m + c
                plt.plot(xnew,ynew,'-')                
                plt.plot(np.ma.argmin(delta)+rotate.start, pit, 'dg')
                plt.plot(idx, alt_std[idx], 'dr')
                plt.show()
                plt.clf()
                '''

            except:
                # If something odd about the data causes a problem with this
                # technique, use a simpler solution. This can give
                # significantly erroneous results in the case of sloping
                # runways, but it's the most robust technique.
                pit = np.ma.min(alt_std)
            alt_result = alt_std - pit
            return np.ma.maximum(alt_result, 0.0)

        if alt_rad is None or np.ma.count(alt_rad)==0:
            # This backstop trap for negative values is necessary as aircraft
            # without rad alts will indicate negative altitudes as they land.
            if mode != 'land':
                return alt_std - high_gnd
            else:
                return shift_alt_std()


        if mode=='over_gnd' and (low_hb-high_gnd)>100.0:
            return alt_std - high_gnd

        
        alt_rad_aal = np.ma.maximum(alt_rad, 0.0)
        #x = np.ma.clump_unmasked(np.ma.masked_outside(alt_rad_aal, 0.1, 100.0))
        #ralt_sections = [y for y in x if np.ma.max(alt_rad[y]>BOUNCED_LANDING_THRESHOLD)]
        ralt_sections = np.ma.clump_unmasked(np.ma.masked_greater(alt_rad_aal, 100.0))
        #ralt_sections = [y for y in x if np.ma.max(alt_rad[y]>BOUNCED_LANDING_THRESHOLD)]

        if len(ralt_sections)==0:
            # Either Altitude Radio did not drop below 100, or did not get
            # above 100. Either way, we are better off working with just the
            # pressure altitude signal.
            return shift_alt_std()

        baro_sections = slices_not(ralt_sections, begin_at=0,
                                   end_at=len(alt_std))

        for ralt_section in ralt_sections:
            alt_result[ralt_section] = alt_rad_aal[ralt_section]

            for baro_section in baro_sections:
                begin_index = baro_section.start
            
                if ralt_section.stop == baro_section.start:
                    # Avoid indexing beyond the end of the data.
                    ending = min(begin_index + 60, len(alt_std), len(alt_rad)) 
                    alt_diff = (alt_std[begin_index:ending] -
                                alt_rad[begin_index:ending])
                    slip, up_diff = first_valid_sample(alt_diff)
                    if slip is None:
                        up_diff = 0.0
                    else:
                        # alt_std is invalid at the point of handover
                        # so stretch the radio signal until we can
                        # handover.
                        fix_slice = slice(begin_index,
                                          begin_index + slip)
                        alt_result[fix_slice] = alt_rad[fix_slice]
                        begin_index += slip
            
                    alt_result[begin_index:] = \
                        alt_std[begin_index:] - up_diff
                
        return alt_result

    def derive(self, alt_rad=P('Altitude Radio'),
               alt_std=P('Altitude STD Smoothed'),
               speedies=S('Fast')):
        # Altitude Radio taken as the prime reference to ensure the minimum
        # ground clearance passing peaks is accurately reflected. Alt AAL
        # forced to 2htz

        # alt_aal will be zero on the airfield, so initialise to zero.
        alt_aal = np_ma_zeros_like(alt_std.array)

        for speedy in speedies:
            quick = speedy.slice
            if speedy.slice == slice(None, None, None):
                self.array = alt_aal
                break

            # We set the minimum height for detecting flights to 500 ft. This
            # ensures that low altitude "hops" are still treated as complete
            # flights while more complex flights are processed as climbs and
            # descents of 500 ft or more.
            alt_idxs, alt_vals = cycle_finder(alt_std.array[quick],
                                              min_step=500)

            # Reference to start of arrays for simplicity hereafter.
            if alt_idxs == None:
                continue

            alt_idxs += quick.start or 0

            n = 0
            dips = []
            # List of dicts, with each sublist containing:

            # 'type' of item 'land' or 'over_gnd' or 'high'

            # 'slice' for this part of the data
            # if 'type' is 'land' the land section comes at the beginning of the
            # slice (i.e. takeoff slices are normal, landing slices are
            # reversed)
            # 'over_gnd' or 'air' are normal slices.

            # 'alt_std' as:
            # 'land' = the pressure altitude on the ground
            # 'over_gnd' = the pressure altitude when flying closest to the
            #              ground
            # 'air' = the lowest pressure altitude in this slice

            # 'highest_ground' in this area
            # 'land' = the pressure altitude on the ground
            # 'over_gnd' = the pressure altitude minus the radio altitude when
            #              flying closest to the ground
            # 'air' = None (the aircraft was too high for the radio altimeter to
            #         register valid data

            n_vals = len(alt_vals)
            while n < n_vals - 1:
                alt = alt_vals[n]
                alt_idx = alt_idxs[n]
                next_alt = alt_vals[n + 1]
                next_alt_idx = alt_idxs[n + 1]

                if next_alt > alt:
                    # Rising section.
                    dips.append({
                        'type': 'land',
                        'slice': slice(quick.start, next_alt_idx),
                        # was 'slice': slice(alt_idx, next_alt_idx),
                        'alt_std': alt,
                        'highest_ground': alt,
                    })
                    n += 1
                    continue

                if n + 2 >= n_vals:
                    # Falling section. Slice it backwards to use the same code
                    # as for takeoffs.
                    dips.append({
                        'type': 'land',
                        'slice': slice(quick.stop, alt_idx - 1, -1),
                        # was 'slice': slice(next_alt_idx - 1, alt_idx - 1, -1),
                        'alt_std': next_alt,
                        'highest_ground': next_alt,
                    })
                    n += 1
                    continue

                if alt_vals[n + 2] > next_alt:
                    # A down and up section.
                    down_up = slice(alt_idx, alt_idxs[n + 2])
                    # Is radio altimeter data both supplied and valid in this
                    # range?
                    if alt_rad and np.ma.count(alt_rad.array[down_up]) > 0:
                        # Let's find the lowest rad alt reading
                        # (this may not be exactly the highest ground, but
                        # it was probably the point of highest concern!)
                        arg_hg_max = \
                            np.ma.argmin(alt_rad.array[down_up]) + \
                            alt_idxs[n]
                        hg_max = alt_std.array[arg_hg_max] - \
                            alt_rad.array[arg_hg_max]
                        if np.ma.count(hg_max):
                            # The rad alt measured height above a peak...
                            dips.append({
                                'type': 'over_gnd',
                                'slice': down_up,
                                'alt_std': alt_std.array[arg_hg_max],
                                'highest_ground': hg_max,
                            })
                    else:
                        # We have no rad alt data we can use.
                        # TODO: alt_std code needs careful checking.
                        if dips:
                            prev_dip = dips[-1]
                        if dips and prev_dip['type'] == 'high':
                            # Join this dip onto the previous one
                            prev_dip['slice'] = \
                                slice(prev_dip['slice'].start,
                                      alt_idxs[n + 2])
                            prev_dip['alt_std'] = \
                                min(prev_dip['alt_std'],
                                    next_alt)
                        else:
                            dips.append({
                                'type': 'high',
                                'slice': down_up,
                                'alt_std': next_alt,
                                'highest_ground': next_alt,
                            })
                    n += 2
                else:
                    raise ValueError('Problem in Altitude AAL where data '
                                     'should dip, but instead has a peak.')

            for n, dip in enumerate(dips):
                if dip['type'] == 'high':
                    if n == 0:
                        if len(dips) == 1:
                            # Arbitrary offset in indeterminate case.
                            dip['alt_std'] = dip['highest_ground'] + 1000
                        else:
                            next_dip = dips[n + 1]
                            dip['highest_ground'] = \
                                dip['alt_std'] - next_dip['alt_std'] + \
                                next_dip['highest_ground']
                    elif n == len(dips) - 1:
                        prev_dip = dips[n - 1]
                        dip['highest_ground'] = \
                            dip['alt_std'] - prev_dip['alt_std'] + \
                            prev_dip['highest_ground']
                    else:
                        # Here is the most commonly used, and somewhat
                        # arbitrary code. For a dip where no radio
                        # measurement of the ground is available, what height
                        # can you use as the datum? The lowest ground
                        # elevation in the preceding and following sections
                        # is practical, a little optimistic perhaps, but
                        # useable until we find a case otherwise.
                        next_dip = dips[n + 1]
                        prev_dip = dips[n - 1]
                        dip['highest_ground'] = min(prev_dip['highest_ground'],
                                                    next_dip['highest_ground'])

            for dip in dips:
                if alt_rad:
                    alt_aal[dip['slice']] = \
                        self.compute_aal(dip['type'],
                                         alt_std.array[dip['slice']],
                                         dip['alt_std'],
                                         dip['highest_ground'],
                                         alt_rad=alt_rad.array[dip['slice']])
                else:
                    alt_aal[dip['slice']] = \
                        self.compute_aal(dip['type'],
                                         alt_std.array[dip['slice']],
                                         dip['alt_std'], dip['highest_ground'])
                      
        # Reset end sections
        alt_aal[quick.start:alt_idxs[0]+1] = 0.0
        alt_aal[alt_idxs[-1]+1:quick.stop] = 0.0
        self.array = alt_aal
        

class AltitudeAALForFlightPhases(DerivedParameterNode):
    name = 'Altitude AAL For Flight Phases'
    units = 'ft'

    # This parameter repairs short periods of masked data, making it suitable
    # for detecting altitude bands on the climb and descent. The parameter
    # should not be used to compute KPV values themselves, to avoid using
    # interpolated values in an event.

    def derive(self, alt_aal=P('Altitude AAL')):
        self.array = repair_mask(alt_aal.array, repair_duration=None)




class AltitudeRadio(DerivedParameterNode):
    """
    There is a wide variety of radio altimeter installations with one, two or
    three sensors recorded - each with different timing, sample rate and
    inaccuracies to be compensated. This derive process gathers all the
    available data and passes the blending task to blend_parameters where
    multiple cubic splines are joined with variable weighting to provide an
    optimal combination of the available data.

    :returns Altitude Radio with values typically taken as the mean between
    two valid sensors.
    :type parameter object.
    """

    units = 'ft'
    align = False

    @classmethod
    def can_operate(cls, available):
        return any_of([name for name in cls.get_dependency_names() \
                       if name.startswith('Altitude Radio')], available)

    
    def derive(self,
               source_A = P('Altitude Radio (A)'),
               source_B = P('Altitude Radio (B)'),
               source_C = P('Altitude Radio (C)'),
               source_E = P('Altitude Radio EFIS'),
               source_L = P('Altitude Radio EFIS (L)'),
               source_R = P('Altitude Radio EFIS (R)')):
        sources = [source_A, source_B, source_C, source_E, source_L, source_R]
        self.offset = 0.0
        self.frequency = 4.0
        self.array = blend_parameters(sources,
                                      offset=self.offset, 
                                      frequency=self.frequency)


'''
class AltitudeRadio(DerivedParameterNode):
    """
    There is a wide variety of radio altimeter installations including linear
    and non-linear transducers with various transfer functions, and two or
    three sensors may be installed each with different timing and
    inaccuracies to be compensated.

    The input data is stored 'temporarily' in parameters named Altitude Radio
    (A) to (D), and the frame details are augmented by a frame qualifier
    which identifies which formula to apply.

    :param frame: The frame attribute, e.g. '737-i'
    :type frame: An attribute
    :param frame_qual: The frame qualifier, e.g. 'Altitude_Radio_D226A101_1_16D'
    :type frame_qual: An attribute

    :returns Altitude Radio with values typically taken as the mean between
    two valid sensors.
    :type parameter object.
    """

    units = 'ft'
    align = False

    @classmethod
    def can_operate(cls, available):
        return ('Altitude Radio (A)' in available and
                'Altitude Radio (B)' in available)

    def derive(self, frame = A('Frame'),
               frame_qual = A('Frame Qualifier'),
               source_A = P('Altitude Radio (A)'),
               source_B = P('Altitude Radio (B)'),
               source_C = P('Altitude Radio (C)'),
               source_E = P('Altitude Radio EFIS'),
               source_L = P('Altitude Radio EFIS (L)'),
               source_R = P('Altitude Radio EFIS (R)')):

        frame_name = frame.value if frame else ''
        frame_qualifier = frame_qual.value if frame_qual else None

        # 737-1 & 737-i has Altitude Radio recorded.
        if frame_name in ['737-3']:
            # Select the source without abnormal latency.
            self.array = source_B.array

        elif frame_name in ['737-3A', '737-3B', '737-3C', '757-DHL', '767-3A']:
            # 737-3* comment:
            # Alternate samples (A) for this frame have latency of over 1
            # second, so do not contribute to the height measurements
            # available. For this reason we only blend the two good sensors.
            # - discovered with 737-3C and 3A/3B have same LFL for this param.

            # 757-DHL comment:
            # Altitude Radio (B) comes from the Right altimeter, and is
            # sampled in word 26 of the frame. Altitude Radio (C) comes from
            # the Centre altimeter, is sample in word 104. Altitude Radio (A)
            # comes from the EFIS system, and includes excessive latency so
            # is not used.
            
            #767-3A frame comment:
            # The normal operation is to use the altitude radio signal from
            # all four sensors, extracted directly by the lfl. This routine
            # is only called upon if that fails and a second attempt to
            # provide valid data is required. It may not, of course, be
            # susccesful.
            self.array, self.frequency, self.offset = \
                blend_two_parameters(source_B, source_C)

        elif frame_name in ['737-4', '737-4_Analogue', 'F28_AV94_0252']:
            if frame_qualifier and 'Altitude_Radio_EFIS' in frame_qualifier:
                self.array, self.frequency, self.offset = \
                    blend_two_parameters(source_L, source_R)
            else:
                self.array, self.frequency, self.offset = \
                    blend_two_parameters(source_A, source_B)

        elif frame_name in ('737-5', '737-5_NON-EIS'):
            ##if frame_qualifier and 'Altitude_Radio_EFIS' in frame_qualifier or\
               ##frame_qualifier and 'Altitude_Radio_ARINC_552' in frame_qualifier:
            self.array, self.frequency, self.offset = \
                blend_two_parameters(source_A, source_B)
            ##elif frame_qualifier and 'Altitude_Radio_None' in frame_qualifier:
                ##pass # Some old 737 aircraft have no rad alt recorded.
            ##else:
                ##raise ValueError,'737-5 frame Altitude Radio qualifier not recognised.'

        elif frame_name in ['CRJ-700-900', 'E135-145']:
            self.array, self.frequency, self.offset = \
                blend_two_parameters(source_A, source_B)
        
        elif frame_name in ['767-2227000-59B']:
            # The four sources, Left, Centre, EFIS and Right are sampled in every frame.
            self.array = repair_mask(merge_sources(source_A.array, 
                                                   source_B.array, 
                                                   source_E.array, 
                                                   source_C.array)
                                     )
            self.frequency = source_A.frequency * 4.0
            self.offset = source_A.offset

        elif frame_name in ['A320_SFIM_ED45_CFM']:
            self.array, self.frequency, self.offset = \
                blend_two_parameters(source_A, source_B)

        else:
            raise DataFrameError(self.name, frame_name)
'''


class AltitudeSTDSmoothed(DerivedParameterNode):
    """
    :param frame: The frame attribute, e.g. '737-i'
    :type frame: An attribute

    :returns Altitude STD Smoothed as a local average where the original source is unacceptable, but unchanged otherwise.
    :type parameter object.
    """

    name = "Altitude STD Smoothed"
    units = 'ft'

    @classmethod
    def can_operate(cls, available):
        return 'Altitude STD' in available

    def derive(self, fine = P('Altitude STD (Fine)'), alt = P('Altitude STD'),
               frame = A('Frame')):

        frame_name = frame.value if frame else ''

        if frame_name in ['737-i', '757-DHL'] or \
           frame_name.startswith('737-6'):
            # The altitude signal is measured in steps of 32 ft (10ft for
            # 757-DHL) so needs smoothing. A 5-point Gaussian distribution
            # was selected as a balance between smoothing effectiveness and
            # excessive manipulation of the data.
            gauss = [0.054488683, 0.244201343, 0.402619948, 0.244201343, 0.054488683]
            self.array = moving_average(alt.array, window=5, weightings=gauss)
        elif frame_name in ['E135-145', 'L382-Hercules']:
            # Here two sources are sampled alternately, so this form of
            # weighting merges the two to create a smoothed average.
            self.array = moving_average(alt.array, window=3,
                                        weightings=[0.25,0.5,0.25], pad=True)
        elif frame_name in ['747-200-GE']:
            # Rollover is at 2^12 x resolution of fine part.
            self.array = straighten_altitudes(fine.array, alt.array, 5000)
        elif frame_name in ['A300-203-B4']:
            # Fine part synchro used to compute altitude, as this does not match the coarse part synchro.
            self.array = straighten_altitudes(fine.array, alt.array, 5000)
        else:
            self.array = alt.array

# TODO: Account for 'Touch & Go' - need to adjust QNH for additional airfields!
class AltitudeQNH(DerivedParameterNode):
    '''
    This altitude is above mean sea level. From the takeoff airfield to the
    highest altitude above airfield, the altitude QNH is referenced to the
    takeoff airfield elevation, and from that point onwards it is referenced
    to the landing airfield elevation.

    We can determine the elevation in the following ways:

    1. Take the average elevation between the start and end of the runway.
    2. Take the general elevation of the airfield.

    If we can only determine the takeoff elevation, the landing elevation
    will using the same value as the error will be the difference in pressure
    altitude between the takeoff and landing airports on the day which is
    likely to be less than forcing it to 0. Therefore landing elevation is
    used if the takeoff elevation cannot be determined.

    If we are unable to determine either the takeoff or landing elevations,
    we use the Altitude AAL parameter.
    '''

    name = 'Altitude QNH'
    units = 'ft'

    @classmethod
    def can_operate(cls, available):
        return 'Altitude AAL' in available and 'Altitude Peak' in available

    def derive(self, alt_aal=P('Altitude AAL'), alt_peak=KTI('Altitude Peak'),
            l_apt=A('FDR Landing Airport'), l_rwy=A('FDR Landing Runway'),
            t_apt=A('FDR Takeoff Airport'), t_rwy=A('FDR Takeoff Runway')):
        '''
        We attempt to adjust Altitude AAL by adding elevation at takeoff and
        landing. We need to know the takeoff and landing runway to get the most
        precise elevation, falling back to the airport elevation if they are
        not available.
        '''
        alt_qnh = np.ma.copy(alt_aal.array)  # copy only required for test case

        # Attempt to determine elevation at takeoff:
        t_elev = None
        if t_rwy:
            t_elev = self._calc_rwy_elev(t_rwy.value)
        if t_elev is None and t_apt:
            t_elev = self._calc_apt_elev(t_apt.value)

        # Attempt to determine elevation at landing:
        l_elev = None
        if l_rwy:
            l_elev = self._calc_rwy_elev(l_rwy.value)
        if l_elev is None and l_apt:
            l_elev = self._calc_apt_elev(l_apt.value)

        if t_elev is None and l_elev is None:
            self.warning("No Takeoff or Landing elevation, using Altitude AAL")
            self.array = alt_qnh
            return  # BAIL OUT!
        elif t_elev is None:
            self.warning("No Takeoff elevation, using %dft at Landing", l_elev)
            smooth = False
            t_elev = l_elev
        elif l_elev is None:
            self.warning("No Landing elevation, using %dft at Takeoff", t_elev)
            smooth = False
            l_elev = t_elev
        else:
            # both have valid values
            smooth = True

        # Break the "journey" at the "midpoint" - actually max altitude aal -
        # and be sure to account for rise/fall in the data and stick the peak
        # in the correct half:
        peak = alt_peak.get_first()  # NOTE: Fix for multiple approaches...
        fall = alt_aal.array[peak.index - 1] > alt_aal.array[peak.index + 1]
        peak = peak.index
        if fall:
            peak += int(fall)

        # Add the elevation at takeoff to the climb portion of the array:
        alt_qnh[:peak] += t_elev

        # Add the elevation at landing to the descent portion of the array:
        alt_qnh[peak:] += l_elev

        # Attempt to smooth out any ugly transitions due to differences in
        # pressure so that we don't get horrible bumps in visualisation:
        if smooth:
            # step jump transforms into linear slope
            delta = np.ma.ptp(alt_qnh[peak - 1:peak + 1])
            width = ceil(delta * alt_aal.frequency / 3)
            window = slice(peak - width, peak + width + 1)
            alt_qnh[window] = np.ma.masked
            repair_mask(
                array=alt_qnh,
                repair_duration=window.stop - window.start,
            )

        self.array = alt_qnh

    @staticmethod
    def _calc_apt_elev(apt):
        '''
        '''
        return apt.get('elevation')

    @staticmethod
    def _calc_rwy_elev(rwy):
        '''
        '''
        elev_s = rwy.get('start', {}).get('elevation')
        elev_e = rwy.get('end', {}).get('elevation')
        if elev_s is None:
            return elev_e
        if elev_e is None:
            return elev_s
        # FIXME: Determine based on liftoff/touchdown coordinates?
        return (elev_e + elev_s) / 2


'''
class AltitudeSTD(DerivedParameterNode):
    """
    This section allows for manipulation of the altitude recordings from
    different types of aircraft. Problems often arise due to combination of
    the fine and coarse parts of the data and many different types of
    correction have been developed to cater for these cases.
    """
    name = 'Altitude STD'
    units = 'ft'
    @classmethod
    def can_operate(cls, available):
        high_and_low = 'Altitude STD (Coarse)' in available and \
            'Altitude STD (Fine)' in available
        coarse_and_ivv = 'Altitude STD (Coarse)' in available and \
            'Vertical Speed' in available
        return high_and_low or coarse_and_ivv

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

    def _coarse_and_ivv(self, alt_std_coarse, ivv):
        alt_std_with_lag = first_order_lag(alt_std_coarse.array, 10,
                                           alt_std_coarse.hz)
        mask = np.ma.mask_or(alt_std_with_lag.mask, ivv.array.mask)
        return np.ma.masked_array(alt_std_with_lag + (ivv.array / 60.0),
                                  mask=mask)

    def derive(self, alt_std_coarse=P('Altitude STD (Coarse)'),
               alt_std_fine=P('Altitude STD (Fine)'),
               ivv=P('Vertical Speed')):
        if alt_std_high and alt_std_low:
            self.array = self._high_and_low(alt_std_coarse, alt_std_fine)
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
        elif alt_std_coarse and ivv:
            self.array = self._coarse_and_ivv(alt_std_coarse, ivv)
            #ALT_STDC = (last_alt_std * 0.9) + (ALT_STD * 0.1) + (IVVR / 60.0)
            '''


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
        pitch_rad = pitch.array * deg2rad
        # Now apply the offset
        gear2tail = dist_gear_to_tail.value * METRES_TO_FEET
        ground2tail = ground_to_tail.value * METRES_TO_FEET
        # Prepare to add back in the negative rad alt reading as the aircraft
        # settles on its oleos
        min_rad = np.ma.min(alt_rad.array)
        self.array = (alt_rad.array + ground2tail -
                      np.ma.sin(pitch_rad) * gear2tail - min_rad)


##############################################################################
# Automated Systems

class APEngaged(MultistateDerivedParameterNode):
    '''
    Determines if *any* of the "AP (*) Engaged" parameters are recording the
    state of Engaged.
    
    This is a discrete with only the Engaged state.
    '''

    name = 'AP Engaged'
    align = False  #TODO: Should this be here?
    values_mapping = {0: '-', 1: 'Engaged'}

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self, ap1=M('AP (1) Engaged'),
                     ap2=M('AP (2) Engaged'),
                     ap3=M('AP (3) Engaged')):
        stacked = vstack_params_where_state(
            (ap1, 'Engaged'),
            (ap2, 'Engaged'),
            (ap3, 'Engaged'),
            )
        self.array = stacked.any(axis=0)
        if ap1:
            self.frequency = ap1.frequency
        elif ap2:
            self.frequency = ap2.frequency
        else:
            self.frequency = ap3.frequency
        self.offset = offset_select('mean', [ap1, ap2, ap3])


class Autoland(MultistateDerivedParameterNode):
    '''
    Assess the number of autopilot systems engaged to see if the autoland is
    in Dual or Triple mode.

    Airbus and Boeing = 1 autopilot at a time except when "Land" mode
    selected when 2 (Dual) or 3 (Triple) can be engaged. Airbus favours only
    2 APs, Boeing is happier with 3 though some older types may only have 2.
    '''
    align = False  #TODO: Should this be here?
    values_mapping = {0:'-', 2: 'Dual', 3: 'Triple'}

    @classmethod
    def can_operate(cls, available):
        return len(available) >= 2

    def derive(self, ap1=M('AP (1) Engaged'),
                     ap2=M('AP (2) Engaged'),
                     ap3=M('AP (3) Engaged')):
        stacked = vstack_params_where_state(
            (ap1, 'Engaged'),
            (ap2, 'Engaged'),
            (ap3, 'Engaged'),
            )
        self.array = stacked.sum(axis=0)
        # Force single autopilot to 0 state to avoid confusion
        self.array[self.array == 1] = 0
        # Assume all are sampled at the same frequency
        self.frequency = ap1.frequency
        self.offset = offset_select('mean', [ap1, ap2, ap3])


class ClimbForFlightPhases(DerivedParameterNode):
    """
    This computes climb segments, and resets to zero as soon as the aircraft
    descends. Very useful for measuring climb after an aborted approach etc.
    """

    units = 'ft'

    def derive(self, alt_std=P('Altitude STD Smoothed'), airs=S('Fast')):
        self.array = np.ma.zeros(len(alt_std.array))
        repair_mask(alt_std.array) # Remove small sections of corrupt data
        for air in airs:
            deltas = np.ma.ediff1d(alt_std.array[air.slice], to_begin=0.0)
            ups = np.ma.clump_unmasked(np.ma.masked_less(deltas,0.0))
            for up in ups:
                self.array[air.slice][up] = np.ma.cumsum(deltas[up])


class Daylight(MultistateDerivedParameterNode):
    '''
    Calculate Day or Night based upon Civil Twilight.
    
    FAA Regulation FAR 1.1 defines night as: "Night means the time between
    the end of evening civil twilight and the beginning of morning civil
    twilight, as published in the American Air Almanac, converted to local
    time.

    EASA EU OPS 1 Annex 1 item (76) states: 'night' means the period between
    the end of evening civil twilight and the beginning of morning civil
    twilight or such other period between sunset and sunrise as may be
    prescribed by the appropriate authority, as defined by the Member State;

    CAA regulations confusingly define night as 30 minutes either side of
    sunset and sunrise, then include a civil twilight table in the AIP.

    With these references, it was decided to make civil twilight the default.
    '''
    align = True
    align_frequency = 0.25
    align_offset = 0.0

    values_mapping = {
        0 : 'Night',
        1 : 'Day'
        }

    def derive(self,
               latitude=P('Latitude Smoothed'),
               longitude=P('Longitude Smoothed'),
               start_datetime=A('Start Datetime'),
               duration=A('HDF Duration')):
        # Set default to 'Day'
        array_len = duration.value * self.frequency
        self.array = np.ma.ones(array_len)
        for step in xrange(int(array_len)):
            curr_dt = datetime_of_index(start_datetime.value, step, 1)
            lat = latitude.array[step]
            lon = longitude.array[step]
            if lat and lon:
                if not is_day(curr_dt, lat, lon):
                    # Replace values with Night
                    self.array[step] = 0
                else:
                    continue  # leave array as 1
            else:
                # either is masked or recording 0.0 which is invalid too
                self.array[step] = np.ma.masked


class DescendForFlightPhases(DerivedParameterNode):
    """
    This computes descent segments, and resets to zero as soon as the aircraft
    climbs Used for measuring descents, e.g. following a suspected level bust.
    """

    units = 'ft'

    def derive(self, alt_std=P('Altitude STD Smoothed'), airs=S('Fast')):
        self.array = np.ma.zeros(len(alt_std.array))
        repair_mask(alt_std.array) # Remove small sections of corrupt data
        for air in airs:
            deltas = np.ma.ediff1d(alt_std.array[air.slice], to_begin=0.0)
            downs = np.ma.clump_unmasked(np.ma.masked_greater(deltas,0.0))
            for down in downs:
                self.array[air.slice][down] = np.ma.cumsum(deltas[down])


class AOA(DerivedParameterNode):

    align = False
    name = 'AOA'
    units = 'deg'

    def derive(self, aoa_l=P('AOA (L)'), aoa_r=P('AOA (R)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(aoa_l, aoa_r)


class ControlColumn(DerivedParameterNode):
    '''
    The position of the control column blended from the position of the captain
    and first officer's control columns.
    '''
    align = False
    units = 'deg'

    def derive(self,
               posn_capt=P('Control Column (Capt)'),
               posn_fo=P('Control Column (FO)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(posn_capt, posn_fo)


class ControlColumnCapt(DerivedParameterNode):
    # See ElevatorLeft for explanation
    name = 'Control Column (Capt)'
    @classmethod
    def can_operate(cls, available):
        return any_of(('Control Column (Capt) Potentiometer', 
                       'Control Column (Capt) Synchro'), available)
    
    def derive(self, pot=P('Control Column (Capt) Potentiometer'),
               synchro=P('Control Column (Capt) Synchro')):
        synchro_samples = 0
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array

class ControlColumnFO(DerivedParameterNode):
    # See ElevatorLeft for explanation
    name = 'Control Column (FO)'
    @classmethod
    def can_operate(cls, available):
        return any_of(('Control Column (FO) Potentiometer', 
                       'Control Column (FO) Synchro'), available)
    
    def derive(self, pot=P('Control Column (FO) Potentiometer'),
               synchro=P('Control Column (FO) Synchro')):
        synchro_samples = 0
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array


class ControlColumnForce(DerivedParameterNode):
    '''
    The combined force from the captain and the first officer.
    '''

    units = 'lbf'

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
    
    On the ATR42 there is the option of potentiometer or synchro input.
    '''
    @classmethod
    def can_operate(cls, available):
        return all_of(('Control Wheel (Capt)','Control Wheel (FO)'), available)\
               or\
               any_of(('Control Wheel Synchro','Control Wheel Potentiometer'), available)

    align = False
    units = 'deg'

    def derive(self,
               posn_capt=P('Control Wheel (Capt)'),
               posn_fo=P('Control Wheel (FO)'),
               pot=P('Control Wheel Potentiometer'),
               synchro=P('Control Wheel Synchro')):

        # Usually we are blending two sensors
        if posn_capt and posn_fo:
            self.array, self.frequency, self.offset = \
                blend_two_parameters(posn_capt, posn_fo)
            
        # Less commonly we are selecting from a single source
        else:
            synchro_samples = 0
            if synchro:
                synchro_samples = np.ma.count(synchro.array)
                self.array = synchro.array
            if pot:
                pot_samples = np.ma.count(pot.array)
                if pot_samples>synchro_samples:
                    self.array = pot.array
        
class DistanceToLanding(DerivedParameterNode):
    """
    Ground distance to cover before touchdown.

    Note: This parameter gets closer to zero approaching the final touchdown,
    but then increases as the aircraft decelerates on the runway.
    """

    units = 'nm'

    # Q: Is this distance to final landing, or distance to each approach
    # destination (i.e. resets once reaches point of go-around)

    def derive(self, dist=P('Distance Travelled'), tdwns=KTI('Touchdown')):
        if tdwns:
            dist_flown_at_tdwn = dist.array[tdwns.get_last().index]
            self.array = np.ma.abs(dist_flown_at_tdwn - dist.array)
        else:
            self.array = np.zeros_like(dist.array)
            self.array.mask = True


class DistanceTravelled(DerivedParameterNode):
    '''
    Distance travelled in Nautical Miles. Calculated using integral of
    Groundspeed.
    '''

    units = 'nm'

    def derive(self, gspd=P('Groundspeed')):
        self.array = integrate(gspd.array, gspd.frequency, scale=1.0 / 3600.0)


class Drift(DerivedParameterNode):

    align = False
    units = 'deg'

    def derive(self, drift_1=P('Drift (1)'), drift_2=P('Drift (2)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(drift_1, drift_2)



################################################################################
# Brakes

class BrakePressure(DerivedParameterNode):
    """
    Gather the recorded brake parameters and convert into a single analogue.

    This node allows for expansion for different types, and possibly
    operation in primary and standby modes.
    """

    align = False

    @classmethod
    def can_operate(cls, available):
        return ('Brake (L) Press' in available and \
                'Brake (R) Press' in available)

    def derive(self, brake_L=P('Brake (L) Press'), brake_R=P('Brake (R) Press')):
        self.array, self.frequency, self.offset = blend_two_parameters(brake_L, brake_R)

################################################################################
# Pack Valves


class PackValvesOpen(MultistateDerivedParameterNode):
    '''
    Integer representation of the combined pack configuration.
    '''

    align = False
    name = 'Pack Valves Open'

    values_mapping = {
        0: 'All closed',
        1: 'One engine low flow',
        2: 'Flow level 2',
        3: 'Flow level 3',
        4: 'Both engines high flow',
    }

    @classmethod
    def can_operate(cls, available):
        '''
        '''
        # Works with both 'ECS Pack (1) On' and 'ECS Pack (2) On' ECS Pack High Flows are optional
        return all_of(['ECS Pack (1) On', 'ECS Pack (2) On' ], available)

    def derive(self,
            p1=P('ECS Pack (1) On'), p1h=P('ECS Pack (1) High Flow'),
            p2=P('ECS Pack (2) On'), p2h=P('ECS Pack (2) High Flow')):
        '''
        '''
        # TODO: account properly for states/frame speciffic fixes
        # Sum the open engines, allowing 1 for low flow and 1+1 for high flow
        # each side.
        flow = p1.array.raw + p2.array.raw
        if p1h and p2h:
            flow = p1.array.raw * (1 + p1h.array.raw) \
                 + p2.array.raw * (1 + p2h.array.raw)
        self.array = flow
        self.offset = offset_select('mean', [p1, p1h, p2, p2h])


################################################################################
# Engine Running

class Eng_AllRunning(MultistateDerivedParameterNode):
    '''
    Discrete parameter describing when all available engines are running.
    
    TODO: Include Fuel cut-off switch if recorded?
    
    TODO: Confirm that all engines were recording for the N2 Min / Fuel Flow
    Min parameters - theoretically there could be only three engines in the
    frame for a four engine aircraft. Use "Engine Count".
    
    TODO: Support shutdown for Propellor aircraft that don't record fuel flow.
    '''
    name = 'Eng (*) All Running'
    values_mapping = {
        0 : 'Not Running',
        1 : 'Running',
        }
    
    @classmethod
    def can_operate(cls, available):
        return 'Eng (*) N2 Min' in available or \
               'Eng (*) Fuel Flow Min' in available
    
    def derive(self,
               eng_n2=P('Eng (*) N2 Min'),
               fuel_flow=P('Eng (*) Fuel Flow Min')):
        # TODO: move values to settings
        n2_running = eng_n2.array > 10 if eng_n2 \
            else np.ones_like(fuel_flow.array, dtype=bool)
        fuel_flowing = fuel_flow.array > 50 if fuel_flow \
            else np.ones_like(eng_n2.array, dtype=bool)
        # must have N2 and Fuel Flow if both are available
        self.array = n2_running & fuel_flowing


################################################################################
# Engine EPR


class Eng_EPRAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) EPR Avg'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) EPR'),
               eng2=P('Eng (2) EPR'),
               eng3=P('Eng (3) EPR'),
               eng4=P('Eng (4) EPR')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_EPRMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) EPR Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) EPR'),
               eng2=P('Eng (2) EPR'),
               eng3=P('Eng (3) EPR'),
               eng4=P('Eng (4) EPR')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_EPRMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) EPR Min'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) EPR'),
               eng2=P('Eng (2) EPR'),
               eng3=P('Eng (3) EPR'),
               eng4=P('Eng (4) EPR')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_EPRMinFor5Sec(DerivedParameterNode):
    '''
    Returns the lowest EPR for up to four engines over five seconds.
    '''

    name = 'Eng (*) EPR Min For 5 Sec'
    units = '%'
    align_frequency = 2
    align_offset = 0

    def derive(self,
               eng_epr_min=P('Eng (*) EPR Min')):

        #self.array = clip(eng_epr_min.array, 5.0, eng_epr_min.frequency, remove='troughs')
        self.array = second_window(eng_epr_min.array, self.frequency, 5)


################################################################################
# Engine Fire


class Eng_1_Fire(MultistateDerivedParameterNode):
    '''
    Combine on ground and in air fire warnings.
    '''

    name = 'Eng (1) Fire'
    values_mapping = {0: '-', 1: 'Fire'}

    def derive(self,
               fire_gnd=M('Eng (1) Fire On Ground'),
               fire_air=M('Eng (1) Fire In Air')):

        self.array = vstack_params_where_state(
            (fire_gnd, 'Fire'),
            (fire_air, 'Fire'),
        ).any(axis=0)


class Eng_2_Fire(MultistateDerivedParameterNode):
    '''
    Combine on ground and in air fire warnings.
    '''

    name = 'Eng (2) Fire'
    values_mapping = {0: '-', 1: 'Fire'}

    def derive(self,
               fire_gnd=M('Eng (2) Fire On Ground'),
               fire_air=M('Eng (2) Fire In Air')):

        self.array = vstack_params_where_state(
            (fire_gnd, 'Fire'),
            (fire_air, 'Fire'),
        ).any(axis=0)


class Eng_3_Fire(MultistateDerivedParameterNode):
    '''
    Combine on ground and in air fire warnings.
    '''

    name = 'Eng (3) Fire'
    values_mapping = {0: '-', 1: 'Fire'}

    def derive(self,
               fire_gnd=M('Eng (3) Fire On Ground'),
               fire_air=M('Eng (3) Fire In Air')):

        self.array = vstack_params_where_state(
            (fire_gnd, 'Fire'),
            (fire_air, 'Fire'),
        ).any(axis=0)


class Eng_4_Fire(MultistateDerivedParameterNode):
    '''
    Combine on ground and in air fire warnings.
    '''

    name = 'Eng (4) Fire'
    values_mapping = {0: '-', 1: 'Fire'}

    def derive(self,
               fire_gnd=M('Eng (4) Fire On Ground'),
               fire_air=M('Eng (4) Fire In Air')):

        self.array = vstack_params_where_state(
            (fire_gnd, 'Fire'),
            (fire_air, 'Fire'),
        ).any(axis=0)


class Eng_Fire(MultistateDerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Fire'
    values_mapping = {0: '-', 1: 'Fire'}

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=M('Eng (1) Fire'),
               eng2=M('Eng (2) Fire'),
               eng3=M('Eng (3) Fire'),
               eng4=M('Eng (4) Fire')):

        self.array = vstack_params_where_state(
            (eng1, 'Fire'), (eng2, 'Fire'),
            (eng3, 'Fire'), (eng4, 'Fire'),
        ).any(axis=0)


################################################################################
# Engine Fuel Flow


class Eng_FuelFlow(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Fuel Flow'
    units = 'kg/h'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Fuel Flow'),
               eng2=P('Eng (2) Fuel Flow'),
               eng3=P('Eng (3) Fuel Flow'),
               eng4=P('Eng (4) Fuel Flow')):
        # assume all engines Fuel Flow are record at the same frequency
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.sum(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_FuelFlowMin(DerivedParameterNode):
    '''
    The minimum recorded Fuel Flow across all engines.
    
    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) Fuel Flow Min'
    units = 'kg/h'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Fuel Flow'),
               eng2=P('Eng (2) Fuel Flow'),
               eng3=P('Eng (3) Fuel Flow'),
               eng4=P('Eng (4) Fuel Flow')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)

        
###############################################################################
# Fuel Burn


class Eng_1_FuelBurn(DerivedParameterNode):
    '''
    Amount of fuel burnt since the start of the data.
    '''

    name = 'Eng (1) Fuel Burn'
    units = 'kg'

    def derive(self,
               ff=P('Eng (1) Fuel Flow')):

        flow = repair_mask(ff.array)
        self.array = np.ma.array(integrate(flow / 3600.0, ff.frequency))


class Eng_2_FuelBurn(DerivedParameterNode):
    '''
    Amount of fuel burnt since the start of the data.
    '''

    name = 'Eng (2) Fuel Burn'
    units = 'kg'

    def derive(self,
               ff=P('Eng (2) Fuel Flow')):

        flow = repair_mask(ff.array)
        self.array = np.ma.array(integrate(flow / 3600.0, ff.frequency))


class Eng_3_FuelBurn(DerivedParameterNode):
    '''
    Amount of fuel burnt since the start of the data.
    '''

    name = 'Eng (3) Fuel Burn'
    units = 'kg'

    def derive(self,
               ff=P('Eng (3) Fuel Flow')):

        flow = repair_mask(ff.array)
        self.array = np.ma.array(integrate(flow / 3600.0, ff.frequency))


class Eng_4_FuelBurn(DerivedParameterNode):
    '''
    Amount of fuel burnt since the start of the data.
    '''

    name = 'Eng (4) Fuel Burn'
    units = 'kg'

    def derive(self,
               ff=P('Eng (4) Fuel Flow')):

        flow = repair_mask(ff.array)
        self.array = np.ma.array(integrate(flow / 3600.0, ff.frequency))


class Eng_FuelBurn(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Fuel Burn'
    units = 'kg'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Fuel Burn'),
               eng2=P('Eng (2) Fuel Burn'),
               eng3=P('Eng (3) Fuel Burn'),
               eng4=P('Eng (4) Fuel Burn')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.sum(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Gas Temperature


class Eng_GasTempAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Gas Temp Avg'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Gas Temp'),
               eng2=P('Eng (2) Gas Temp'),
               eng3=P('Eng (3) Gas Temp'),
               eng4=P('Eng (4) Gas Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_GasTempMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Gas Temp Max'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Gas Temp'),
               eng2=P('Eng (2) Gas Temp'),
               eng3=P('Eng (3) Gas Temp'),
               eng4=P('Eng (4) Gas Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_GasTempMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Gas Temp Min'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Gas Temp'),
               eng2=P('Eng (2) Gas Temp'),
               eng3=P('Eng (3) Gas Temp'),
               eng4=P('Eng (4) Gas Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine N1


class Eng_N1Avg(DerivedParameterNode):
    '''
    This returns the avaerage N1 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N1 Avg'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_N1Max(DerivedParameterNode):
    '''
    This returns the highest N1 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N1 Max'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_N1Min(DerivedParameterNode):
    '''
    This returns the lowest N1 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N1 Min'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_N1MinFor5Sec(DerivedParameterNode):
    '''
    Returns the lowest N1 for up to four engines over five seconds.
    '''

    name = 'Eng (*) N1 Min For 5 Sec'
    units = '%'
    align_frequency = 2
    align_offset = 0

    def derive(self,
               eng_n1_min=P('Eng (*) N1 Min')):

        #self.array = clip(eng_n1_min.array, 5.0, eng_n1_min.frequency, remove='troughs')
        self.array = second_window(eng_n1_min.array, self.frequency, 5)


################################################################################
# Engine N2


class Eng_N2Avg(DerivedParameterNode):
    '''
    This returns the avaerage N2 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N2 Avg'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_N2Max(DerivedParameterNode):
    '''
    This returns the highest N2 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N2 Max'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_N2Min(DerivedParameterNode):
    '''
    This returns the lowest N2 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N2 Min'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


################################################################################
# Engine N3


class Eng_N3Avg(DerivedParameterNode):
    '''
    This returns the average N3 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N3 Avg'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N3'),
               eng2=P('Eng (2) N3'),
               eng3=P('Eng (3) N3'),
               eng4=P('Eng (4) N3')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_N3Max(DerivedParameterNode):
    '''
    This returns the highest N3 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N3 Max'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N3'),
               eng2=P('Eng (2) N3'),
               eng3=P('Eng (3) N3'),
               eng4=P('Eng (4) N3')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_N3Min(DerivedParameterNode):
    '''
    This returns the lowest N3 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N3 Min'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N3'),
               eng2=P('Eng (2) N3'),
               eng3=P('Eng (3) N3'),
               eng4=P('Eng (4) N3')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


################################################################################
# Engine Np


class Eng_NpAvg(DerivedParameterNode):
    '''
    This returns the average Np in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) Np Avg'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Np'),
               eng2=P('Eng (2) Np'),
               eng3=P('Eng (3) Np'),
               eng4=P('Eng (4) Np')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_NpMax(DerivedParameterNode):
    '''
    This returns the highest Np in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) Np Max'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Np'),
               eng2=P('Eng (2) Np'),
               eng3=P('Eng (3) Np'),
               eng4=P('Eng (4) Np')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_NpMin(DerivedParameterNode):
    '''
    This returns the lowest Np in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) Np Min'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Np'),
               eng2=P('Eng (2) Np'),
               eng3=P('Eng (3) Np'),
               eng4=P('Eng (4) Np')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


################################################################################
# Engine Oil Pressure


class Eng_OilPressAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Press Avg'
    units = 'psi'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Press'),
               eng2=P('Eng (2) Oil Press'),
               eng3=P('Eng (3) Oil Press'),
               eng4=P('Eng (4) Oil Press')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_OilPressMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Press Max'
    units = 'psi'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Press'),
               eng2=P('Eng (2) Oil Press'),
               eng3=P('Eng (3) Oil Press'),
               eng4=P('Eng (4) Oil Press')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_OilPressMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Press Min'
    units = 'psi'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Press'),
               eng2=P('Eng (2) Oil Press'),
               eng3=P('Eng (3) Oil Press'),
               eng4=P('Eng (4) Oil Press')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Oil Quantity


class Eng_OilQtyAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Qty Avg'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Qty'),
               eng2=P('Eng (2) Oil Qty'),
               eng3=P('Eng (3) Oil Qty'),
               eng4=P('Eng (4) Oil Qty')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_OilQtyMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Qty Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Qty'),
               eng2=P('Eng (2) Oil Qty'),
               eng3=P('Eng (3) Oil Qty'),
               eng4=P('Eng (4) Oil Qty')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_OilQtyMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Qty Min'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Qty'),
               eng2=P('Eng (2) Oil Qty'),
               eng3=P('Eng (3) Oil Qty'),
               eng4=P('Eng (4) Oil Qty')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Oil Temperature


class Eng_OilTempAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Temp Avg'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Temp'),
               eng2=P('Eng (2) Oil Temp'),
               eng3=P('Eng (3) Oil Temp'),
               eng4=P('Eng (4) Oil Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        avg_array = np.ma.average(engines, axis=0)
        if np.ma.count(avg_array) != 0:
            self.array = avg_array
            self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])
        else:
            # Some aircraft have no oil temperature sensors installed, so
            # quit now if there is no valid result.
            self.array = np_ma_masked_zeros_like(avg_array)


class Eng_OilTempMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Temp Max'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Temp'),
               eng2=P('Eng (2) Oil Temp'),
               eng3=P('Eng (3) Oil Temp'),
               eng4=P('Eng (4) Oil Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        max_array = np.ma.max(engines, axis=0)
        if np.ma.count(max_array) != 0:
            self.array = max_array
            self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])
        else:
            # Some aircraft have no oil temperature sensors installed, so
            # quit now if there is no valid result.
            self.array = np_ma_masked_zeros_like(max_array)


class Eng_OilTempMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Temp Min'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Temp'),
               eng2=P('Eng (2) Oil Temp'),
               eng3=P('Eng (3) Oil Temp'),
               eng4=P('Eng (4) Oil Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        min_array = np.ma.min(engines, axis=0)
        if np.ma.count(min_array) != 0:
            self.array = min_array
            self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])
        else:
            # Some aircraft have no oil temperature sensors installed, so
            # quit now if there is no valid result.
            self.array = np_ma_masked_zeros_like(min_array)


################################################################################
# Engine Torque


class Eng_TorqueAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Torque Avg'
    units = 'ft.lb'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Torque'),
               eng2=P('Eng (2) Torque'),
               eng3=P('Eng (3) Torque'),
               eng4=P('Eng (4) Torque')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_TorqueMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Torque Max'
    units = 'ft.lb'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Torque'),
               eng2=P('Eng (2) Torque'),
               eng3=P('Eng (3) Torque'),
               eng4=P('Eng (4) Torque')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_TorqueMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Torque Min'
    units = 'ft.lb'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Torque'),
               eng2=P('Eng (2) Torque'),
               eng3=P('Eng (3) Torque'),
               eng4=P('Eng (4) Torque')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Vibration (N1)


class Eng_VibN1Max(DerivedParameterNode):
    '''
    This derived parameter condenses all the available first shaft order
    vibration measurements into a single consolidated value.
    '''

    name = 'Eng (*) Vib N1 Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Vib N1'),
               eng2=P('Eng (2) Vib N1'),
               eng3=P('Eng (3) Vib N1'),
               eng4=P('Eng (4) Vib N1'),
               fan1=P('Eng (1) Vib N1 Fan'),
               fan2=P('Eng (2) Vib N1 Fan'),
               lpt1=P('Eng (1) Vib N1 Turbine'),
               lpt2=P('Eng (2) Vib N1 Turbine')):

        engines = vstack_params(eng1, eng2, eng3, eng4, fan1, fan2, lpt1, lpt2)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4, fan1, fan2, lpt1, lpt2])


################################################################################
# Engine Vibration (N2)


class Eng_VibN2Max(DerivedParameterNode):
    '''
    This derived parameter condenses all the available second shaft order
    vibration measurements into a single consolidated value.
    '''

    name = 'Eng (*) Vib N2 Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Vib N2'),
               eng2=P('Eng (2) Vib N2'),
               eng3=P('Eng (3) Vib N2'),
               eng4=P('Eng (4) Vib N2'),
               hpc1=P('Eng (1) Vib N2 Compressor'),
               hpc2=P('Eng (2) Vib N2 Compressor'),
               hpt1=P('Eng (1) Vib N2 Turbine'),
               hpt2=P('Eng (2) Vib N2 Turbine')):

        engines = vstack_params(eng1, eng2, eng3, eng4, hpc1, hpc2, hpt1, hpt2)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4, hpc1, hpc2, hpt1, hpt2])


################################################################################
# Engine Vibration (N3)


class Eng_VibN3Max(DerivedParameterNode):
    '''
    This derived parameter condenses all the available third shaft order
    vibration measurements into a single consolidated value.
    '''

    name = 'Eng (*) Vib N3 Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Vib N3'),
               eng2=P('Eng (2) Vib N3'),
               eng3=P('Eng (3) Vib N3'),
               eng4=P('Eng (4) Vib N3')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Eng Thrust

class EngThrustModeRequired(MultistateDerivedParameterNode):
    '''
    Combines Eng Thrust Mode Required parameters.
    '''
    
    values_mapping = {
        0: '-',
        1: 'Required',
    }
    
    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)
    
    def derive(self,
               thrust1=P('Eng (1) Thrust Mode Required'),
               thrust2=P('Eng (2) Thrust Mode Required'),
               thrust3=P('Eng (3) Thrust Mode Required'),
               thrust4=P('Eng (4) Thrust Mode Required')):
        
        thrusts = [thrust for thrust in [thrust1,
                                         thrust2,
                                         thrust3,
                                         thrust4] if thrust]
        
        if len(thrusts) == 1:
            self.array = thrusts[0].array
        
        array = MappedArray(np_ma_zeros_like(thrusts[0].array),
                            values_mapping=self.values_mapping)
        
        masks = []
        for thrust in thrusts:
            masks.append(thrust.array.mask)
            array[thrust.array == 'Required'] = 'Required'
        
        array.mask = merge_masks(masks)
        self.array = array
        


################################################################################


class FuelQty(DerivedParameterNode):
    '''
    May be supplanted by an LFL parameter of the same name if available.

    Sum of fuel in left, right and middle tanks where available.
    '''

    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               fuel_qty1=P('Fuel Qty (1)'),
               fuel_qty2=P('Fuel Qty (2)'),
               fuel_qty3=P('Fuel Qty (3)'),
               fuel_qty_aux=P('Fuel Qty (Aux)')):
        params = []
        for param in (fuel_qty1, fuel_qty2, fuel_qty3, fuel_qty_aux):
            if not param:
                continue
            # Repair array masks to ensure that the summed values are not too small
            # because they do not include masked values.
            try:
                param.array = repair_mask(param.array)
            except ValueError as err:
                # Q: Should we be creating a summed Fuel Qty parameter when
                # omitting a masked parameter? The resulting array will contain
                # values lower than expected. The same problem will occur if
                # a parameter has been marked invalid, though we will not
                # be aware of the problem within a derive method.
                self.warning('Skipping %s while calculating %s: %s. Summed '
                             'fuel quantity may be lower than expected.',
                             param, self, err)
            else:
                params.append(param)

        try:
            stacked_params = vstack_params(*params)
            self.array = np.ma.sum(stacked_params, axis=0)
            self.offset = offset_select('mean', params)
        except:
            # In the case where params are all invalid or empty, return an
            # empty array like the last (inherently recorded) array.
            self.array = np_ma_masked_zeros_like(param.array)
            self.offset = 0.0


class FuelQty_Low(MultistateDerivedParameterNode):
    '''
    '''
    name = "Fuel Qty (*) Low"
    
    values_mapping = {
        0: '-',
        1: 'Warning',
    }
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Fuel Qty Low', 'Fuel Qty (1) Low', 'Fuel Qty (2) Low'),
                      available)
        
    def derive(self, fqty = M('Fuel Qty Low'),
               fqty1 = M('Fuel Qty (1) Low'),
               fqty2 = M('Fuel Qty (2) Low')):
        warning = vstack_params_where_state(
            (fqty,  'Warning'),
            (fqty1, 'Warning'),
            (fqty2, 'Warning'),
        )
        self.array = warning.any(axis=0)


###############################################################################
# Landing Gear


class GearDown(MultistateDerivedParameterNode):
    '''
    This Multi-State parameter uses "majority voting" to decide whether the
    gear is up or down.
    
    If Gear (*) Down is not recorded, it will be created from Gear Down
    Selected which is from the cockpit lever.
    
    TODO: Add a transit delay (~10secs) to the selection to when the gear is
    down.
    '''

    align = False
    values_mapping = {
        0: 'Up',
        1: 'Down',
    }

    @classmethod
    def can_operate(cls, available):
        # Can operate with a any combination of parameters available
        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               gl=M('Gear (L) Down'),
               gn=M('Gear (N) Down'),
               gr=M('Gear (R) Down'),
               gear_sel=M('Gear Down Selected')):
        # Join all available gear parameters and use whichever are available.
        if gl or gn or gr:
            v = vstack_params(gl, gn, gr)
            wheels_down = v.sum(axis=0) >= (v.shape[0] / 2.0)
            self.array = np.ma.where(wheels_down, self.state['Down'], self.state['Up'])
        else:
            self.array = gear_sel.array


class GearOnGround(MultistateDerivedParameterNode):
    '''
    Combination of left and right main gear signals.
    '''
    align = False
    values_mapping = {
        0: 'Air',
        1: 'Ground',
    }

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               gl=M('Gear (L) On Ground'),
               gr=M('Gear (R) On Ground')):

        # Note that this is not needed on the following frames which record
        # this parameter directly: 737-4, 737-i

        if gl and gr:
            delta = abs((gl.offset - gr.offset) * gl.frequency)
            if 0.75 < delta or delta < 0.25:
                # If the samples of the left and right gear are close together,
                # the best representation is to map them onto a single
                # parameter in which we accept that either wheel on the ground
                # equates to gear on ground.
                self.array = np.ma.logical_or(gl.array, gr.array)
                self.frequency = gl.frequency
                self.offset = gl.offset
                return
            else:
                # If the paramters are not co-located, then
                # merge_two_parameters creates the best combination possible.
                self.array, self.frequency, self.offset = merge_two_parameters(gl, gr)
                return
        if gl:
            gear = gl
        else:
            gear = gr
        self.array = gear.array
        self.frequency = gear.frequency
        self.offset = gear.offset


class GearDownSelected(MultistateDerivedParameterNode):
    '''
    Derivation of gear selection for aircraft without this separately recorded.
    Where 'Gear Down Selected' is recorded, this derived parameter will be
    skipped automatically.

    In ideal cases, 'Gear Up Selected' is available and we just invert this.
    
    Red warnings are included as the selection may first be indicated by one
    of the red warning lights coming on, rather than the gear status
    changing.
    
    TODO: Add a transit delay (~10secs) to the selection to when the gear is
    down.
    '''

    values_mapping = {
        0: 'Up',
        1: 'Down',
    }

    @classmethod
    def can_operate(cls, available):

        return 'Gear Down' in available

    def derive(self,
               gear_down=M('Gear Down'),
               gear_up_sel=P('Gear Up Selected'),
               gear_warn_l=P('Gear (L) Red Warning'),
               gear_warn_n=P('Gear (N) Red Warning'),
               gear_warn_r=P('Gear (R) Red Warning')):

        if gear_up_sel:
            # Invert the recorded gear up selected parameter.
            self.array = 1-gear_up_sel.array
            self.frequency = gear_up_sel.frequency
            self.offset = gear_up_sel.offset
            return
        
        dn = gear_down.array.raw
        if gear_warn_l and gear_warn_n and gear_warn_r:
            # Join available gear parameters and use whichever are available.
            stack = vstack_params(
                dn,
                gear_warn_l.array.raw,
                gear_warn_n.array.raw,
                gear_warn_r.array.raw,
            )
            wheels_dn = stack.sum(axis=0) > 0
            self.array = np.ma.where(wheels_dn, self.state['Down'], self.state['Up'])
        else:
            self.array = dn
        self.frequency = gear_down.frequency
        self.offset = gear_down.offset


class GearUpSelected(MultistateDerivedParameterNode):
    '''
    Derivation of gear selection for aircraft without this separately recorded.
    Where 'Gear Up Selected' is recorded, this derived parameter will be
    skipped automatically.

    Red warnings are included as the selection may first be indicated by one
    of the red warning lights coming on, rather than the gear status
    changing.
    '''

    values_mapping = {
        0: 'Down',
        1: 'Up',
    }

    @classmethod
    def can_operate(cls, available):

        return 'Gear Down' in available

    def derive(self,
               gear_down=M('Gear Down'),
               gear_warn_l=P('Gear (L) Red Warning'),
               gear_warn_n=P('Gear (N) Red Warning'),
               gear_warn_r=P('Gear (R) Red Warning')):

        up = 1 - gear_down.array.raw
        if gear_warn_l and gear_warn_n and gear_warn_r:
            # Join available gear parameters and use whichever are available.
            stack = vstack_params(
                up,
                gear_warn_l.array.raw,
                gear_warn_n.array.raw,
                gear_warn_r.array.raw,
            )
            wheels_up = stack.sum(axis=0) > 0
            self.array = np.ma.where(wheels_up, self.state['Up'], self.state['Down'])
        else:
            self.array = up
        self.frequency = gear_down.frequency
        self.offset = gear_down.offset


################################################################################

## There is no difference between the two sources, and the sample rate is so low as to make merging pointless.
##class GrossWeight(DerivedParameterNode):
    ##'''
    ##Merges alternate gross weight measurements. 757-DHK frame applies.
    ##'''
    ##units = 'kg'
    ##align = False

    ##def derive(self,
               ##source_L = P('Gross Weight (L)'),
               ##source_R = P('Gross Weight (R)'),
               ##frame = A('Frame')):

        ##if frame.value in ['757-DHL']:
            ##self.array, self.frequency, self.offset = \
                ##blend_two_parameters(source_L, source_R)

        ##else:
            ##raise DataFrameError(self.name, frame.value)

    

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
    units = 'kgs'

    def derive(self, ff=P('Eng (*) Fuel Flow'),
               gw=P('Gross Weight'),
               climbs=S('Climbing'),
               descends=S('Descending'),
               fast=S('Fast')):

        gw_masked = gw.array.copy()
        gw_masked = mask_inside_slices(gw.array, climbs.get_slices())
        gw_masked = mask_inside_slices(gw.array, descends.get_slices())
        gw_masked = mask_outside_slices(gw.array, fast.get_slices())

        gw_nonzero = gw.array.nonzero()[0]

        try:
            gw_valid_index = gw_masked.nonzero()[0][-1]
        except IndexError:
            self.warning(
                "'%s' had no valid samples within '%s' section, but outside "
                "of '%s' and '%s'. Reverting to '%s'.", self.name, fast.name,
                climbs.name, descends.name, gw.name)
            self.array = gw.array
            return

        flow = repair_mask(ff.array)
        fuel_to_burn = np.ma.array(integrate(flow / 3600.0, ff.frequency,
                                             direction='reverse'))

        offset = gw_masked[gw_valid_index] - fuel_to_burn[gw_valid_index]

        self.array = fuel_to_burn + offset

        # Test that the resulting array is sensible compared with Gross Weight.
        test_index = len(gw_nonzero) / 2
        test_difference = \
            abs(gw.array[test_index] - self.array[test_index]) > 1000
        if test_difference > 1000: # Q: Is 1000 too large?
            raise ValueError(
                "'%s' difference from '%s' at half-way point is greater than "
                "'%s': '%s'." % self.name, gw.name, 1000, test_difference)


class Groundspeed(DerivedParameterNode):
    """
    This caters for cases where some preprocessing is required.
    :param frame: The frame attribute, e.g. '737-i'
    :type frame: An attribute
    :returns groundspeed as the mean between two valid sensors.
    :type parameter object.
    """
    units = 'kts'
    align = False

    @classmethod
    def can_operate(cls, available):
        return all_of(('Altitude STD','Groundspeed (1)','Groundspeed (2)'),
                      available)

    def derive(self,
               alt = P('Altitude STD'),
               source_A = P('Groundspeed (1)'),
               source_B = P('Groundspeed (2)'),
               frame = A('Frame')):

        frame_name = frame.value if frame else ''

        if frame_name in ['757-DHL']:
            # The coding in this frame is unique as it only uses two bits for
            # the first digit of the BCD-encoded groundspeed, limiting the
            # recorded value range to 399 kts. At altitude the aircraft can
            # exceed this so a fiddle is required to sort this out.
            altitude = align(alt, source_A) # Caters for different sample rates.
            adjust_A = np.logical_and(source_A.array<200, altitude>8000).data*400
            source_A.array += adjust_A
            adjust_B = np.logical_and(source_B.array<200, altitude>8000).data*400
            source_B.array += adjust_B
            self.array, self.frequency, self.offset = \
                blend_two_parameters(source_A, source_B)

        else:
            raise DataFrameError(self.name, frame_name)


class FlapLever(MultistateDerivedParameterNode):
    '''
    '''

    units = 'deg'

    ##@classmethod
    ##def can_operate(cls, available):
        ##return any_of(('Flap Angle'), available) \
            ##and all_of(('Series', 'Family'), available)

    def derive(self,
               flap_surf=P('Flap Angle'),
               series=A('Series'),
               family=A('Family')):

        try:
            flap_steps = get_flap_map(series.value, family.value)
        except KeyError:
            # no flaps mapping, round to nearest 5 degrees
            self.warning("No flap settings - rounding to nearest 5")
            # round to nearest 5 degrees
            flap_steps = range(0, 50, 5)
        
        self.values_mapping = {f: str(f) for f in flap_steps}

        # Use flap lever position where recorded, otherwise revert to flap surface.
        ##if flap_lvr:
            ### Take the moment the lever passes midway between two flap detents.
            ##self.array = step_values(flap_lvr.array, flap_lvr.frequency, 
                                     ##flap_steps, step_at='midpoint')
        ##else:
            # Take the moment the flap starts to move.
        self.array = step_values(flap_surf.array, flap_surf.frequency, 
                                 flap_steps, step_at='move_start')


class FlapExcludingTransition(MultistateDerivedParameterNode):
    '''
    Specifically designed to cater for maintenance monitoring, this assumes
    that when moving the lower of the start and endpoints of the movement
    apply. This minimises the chance of needing a flap overspeed inspection.
    '''

    units = 'deg'

    def derive(self,
               flap=P('Flap Angle'),
               series=A('Series'),
               family=A('Family')):

        try:
            flap_steps = get_flap_map(series.value, family.value)
        except KeyError:
            # no flaps mapping, round to nearest 5 degrees
            self.warning("No flap settings - rounding to nearest 5")
            # round to nearest 5 degrees
            array = round_to_nearest(flap.array, 5.0)
            flap_steps = {f: str(f) for f in np.ma.unique(array)}
        else:
            array = step_values(flap.array, flap.frequency, flap_steps, 
                                step_at='excluding_transition')
        self.array = array
        self.values_mapping = {f: str(f) for f in flap_steps}


class FlapIncludingTransition(MultistateDerivedParameterNode):
    '''
    Specifically designed to cater for maintenance monitoring, this assumes
    that when moving the higher of the start and endpoints of the movement
    apply. This increases the chance of needing a flap overspeed inspection,
    but provides a more cautious interpretation of the maintenance
    requirements.
    '''

    units = 'deg'

    def derive(self,
               flap=P('Flap Angle'),
               series=A('Series'),
               family=A('Family')):

        try:
            flap_steps = get_flap_map(series.value, family.value)
        except KeyError:
            # no flaps mapping, round to nearest 5 degrees
            self.warning("No flap settings - rounding to nearest 5")
            # round to nearest 5 degrees
            array = round_to_nearest(flap.array, 5.0)
            flap_steps = {f: str(f) for f in np.ma.unique(array)}
        else:
            array = step_values(flap.array, flap.frequency, flap_steps, 
                                step_at='including_transition')
        self.array = array
        self.values_mapping = {f: str(f) for f in flap_steps}

    
class FlapAngle(DerivedParameterNode):
    '''
    Gather the recorded flap parameters and convert into a single analogue.
    '''

    align = False
    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        return any_of((
            'Flap Angle (L)', 'Flap Angle (R)',
            'Flap Angle (L) Inboard', 'Flap Angle (R) Inboard',
        ), available)

    def derive(self,
               flap_A=P('Flap Angle (L)'),
               flap_B=P('Flap Angle (R)'),
               flap_A_inboard=P('Flap Angle (L) Inboard'),
               flap_B_inboard=P('Flap Angle (R) Inboard'),
               frame=A('Frame')):

        frame_name = frame.value if frame else ''
        flap_A = flap_A or flap_A_inboard
        flap_B = flap_B or flap_B_inboard
        
        if frame_name in ['747-200-GE', '747-200-PW', '747-200-AP-BIB']:
            # Only the right inboard flap is instrumented.
            self.array = flap_B.array
        else:
            # By default, blend the two parameters.
            self.array, self.frequency, self.offset = blend_two_parameters(
                flap_A, flap_B)


class Flap(MultistateDerivedParameterNode):
    '''
    Steps raw Flap angle from surface into detents.
    '''

    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        '''
        can operate with Frame and Alt aal if herc or Flap surface
        '''
        if 'Flap Angle' in available:
            # normal use, we require series / family to lookup the detents
            return all_of(('Series', 'Family'), available)
        else:
            # Hercules has no Flap Surface recorded so determines it from AAL
            # TODO: Implement check for the value of Frame for herc
            return all_of(('Frame', 'Altitude AAL'), available)

    def derive(self,
               flap=P('Flap Angle'),
               series=A('Series'),
               family=A('Family'),
               frame=A('Frame'),
               alt_aal=P('Altitude AAL')):

        frame_name = frame.value if frame else None

        if frame_name == 'L382-Hercules':
            self.values_mapping = {0: '0', 50: '50', 100: '100'}
            
            # Flap is not recorded, so invent one of the correct length.
            flap_herc = np_ma_zeros_like(alt_aal.array)

            # Takeoff is normally with 50% flap382
            _, toffs = slices_from_to(alt_aal.array, 0.0,1000.0)
            flap_herc[:toffs[0].stop] = 50.0

            # Assume 50% from 2000 to 1000ft, and 100% thereafter on the approach.
            _, apps = slices_from_to(alt_aal.array, 2000.0,0.0)
            flap_herc[apps[-1].start:] = np.ma.where(alt_aal.array[apps[-1].start:]>1000.0,50.0,100.0)

            self.array = np.ma.array(flap_herc)
            self.frequency, self.offset = alt_aal.frequency, alt_aal.offset

        elif flap:
            try:
                flap_steps = get_flap_map(series.value, family.value)
            except KeyError:
                # no flaps mapping, round to nearest 5 degrees
                self.warning("No flap settings - rounding to nearest 5")
                # round to nearest 5 degrees
                self.array = round_to_nearest(flap.array, 5.0)
                self.values_mapping = {f: str(f) for f in 
                                       np.ma.unique(self.array.raw)}
                if np.ma.masked in self.values_mapping:
                    del self.values_mapping[np.ma.masked]
            else:
                self.values_mapping = {f: str(f) for f in flap_steps}
                self.array = step_values(flap.array, flap.frequency, flap_steps)
        else:
            self.array = None
            self.values_mapping = {}
            self.warning("No Flap, assigning a masked array")
            # We don't want to fail, because some aircraft might not have Flap
            # recorded correctly
            # raise DataFrameError(self.name, frame_name)


'''
class SlatSurface(DerivedParameterNode):
    """
    """
    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),

    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),

    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),

    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),

    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),

    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),
'''

class Slat(DerivedParameterNode):
    """
    Steps raw Slat angle into detents.
    """
    def derive(self, slat=P('Slat Surface'), series=A('Series'), family=A('Family')):
        try:
            slat_steps = get_slat_map(series.value, family.value)
        except KeyError:
            # no slats mapping, round to nearest 5 degrees
            self.warning("No slat settings - rounding to nearest 5")
            # round to nearest 5 degrees
            self.array = round_to_nearest(slat.array, 5.0)
        else:
            self.array = step_values(slat.array, slat.frequency, slat_steps)


class SlopeToLanding(DerivedParameterNode):
    """
    This parameter was developed as part of the Artificical Intelligence
    analysis of approach profiles, 'Identifying Abnormalities in Aircraft
    Flight Data and Ranking their Impact on the Flight' by Dr Edward Smart,
    Institute of Industrial Research, University of Portsmouth.
    http://eprints.port.ac.uk/4141/
    """
    def derive(self, alt_aal=P('Altitude AAL'), dist=P('Distance To Landing')):
        self.array = alt_aal.array / (dist.array * FEET_PER_NM)


class Configuration(MultistateDerivedParameterNode):
    '''
    Parameter for aircraft that use configuration.

    Multi-state with the following mapping::

        {
            0 : '0',
            1 : '1',
            2 : '1+F',
            3 : '1*',
            4 : '2',
            5 : '2*',
            6 : '3',
            7 : '4',
            8 : '5',
            9 : 'Full',
        }

    Some values are based on footnotes in various pieces of documentation:

    - 2(a) corresponds to CONF 1*
    - 3(b) corresponds to CONF 2*

    Note: Does not use the Flap Lever position. This parameter reflects the
    actual configuration state of the aircraft rather than the intended state
    represented by the selected lever position.

    Note: Values that do not map directly to a required state are masked with
    the data being random (memory alocated)
    '''

    values_mapping = {
        0 : '0',
        1 : '1',
        2 : '1+F',
        3 : '1*',
        4 : '2',
        5 : '2*',
        6 : '3',
        7 : '4',
        8 : '5',
        9 : 'Full',
    }

    @classmethod
    def can_operate(cls, available):
        # TODO: Implement check for the value of Family for Airbus
        return all_of(('Slat', 'Flap', 'Series', 'Family'), available)

    def derive(self, slat=P('Slat'), flap=M('Flap'), flaperon=P('Flaperon'),
               series=A('Series'), family=A('Family'), manu=A('Manufacturer')):

        if manu and manu.value != 'Airbus':
            # TODO: remove check once we can check attributes in can_operate
            self.array = np_ma_masked_zeros_like(flap.array)
            return

        mapping = get_conf_map(series.value, family.value)
        qty_param = len(mapping.itervalues().next())
        if qty_param == 3 and not flaperon:
            # potential problem here!
            self.warning("Flaperon not available, so will calculate "
                         "Configuration using only slat and flap")
            qty_param = 2
        elif qty_param == 2 and flaperon:
            # only two items in values tuple
            self.debug("Flaperon available but not required for "
                       "Configuration calculation")
            pass

        #TODO: Scale each parameter individually to ensure uniqueness.
        
        # Sum the required parameters (creates a unique state value at present)
        summed = vstack_params(*(slat, flap, flaperon)[:qty_param]).sum(axis=0)

        # create a placeholder array fully masked
        self.array = MappedArray(np_ma_masked_zeros_like(flap.array), 
                                 self.values_mapping)
        for state, values in mapping.iteritems():
            s = np.ma.sum(values[:qty_param])
            # unmask bits we know about
            self.array[summed == s] = state


'''

TODO: Revise computation of sliding motion

class GroundspeedAlongTrack(DerivedParameterNode):
    """
    Inertial smoothing provides computation of groundspeed data when the
    recorded groundspeed is unreliable. For example, during sliding motion on
    a runway during deceleration. This is not good enough for long period
    computation, but is an improvement over aircraft where the groundspeed
    data stops at 40kn or thereabouts.
    """
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
'''

class HeadingContinuous(DerivedParameterNode):
    """
    For all internal computing purposes we use this parameter which does not
    jump as it passes through North. To recover the compass display, modulus
    (val % 360 in Python) returns the value to display to the user.
    """
    units = 'deg'
    
    def derive(self, head_mag=P('Heading')):
        self.array = repair_mask(straighten_headings(head_mag.array))


# TODO: Absorb this derived parameter into the 'Holding' flight phase.
class HeadingIncreasing(DerivedParameterNode):
    """
    This parameter is computed to allow holding patterns to be identified. As
    the aircraft can enter a hold turning in one direction, then do a
    teardrop and continue with turns in the opposite direction, we are
    interested in the total angular changes, not the sign of these changes.
    """
    units = 'deg'
    
    def derive(self, head=P('Heading Continuous')):
        rot = np.ma.ediff1d(head.array, to_begin = 0.0)
        self.array = integrate(np.ma.abs(rot), head.frequency)


class HeadingTrueContinuous(DerivedParameterNode):
    '''
    For all internal computing purposes we use this parameter which does not
    jump as it passes through North. To recover the compass display, modulus
    (val % 360 in Python) returns the value to display to the user.
    '''
    units = 'deg'
    
    def derive(self, hdg=P('Heading True')):
        self.array = repair_mask(straighten_headings(hdg.array))


class Heading(DerivedParameterNode):
    """
    Compensates for magnetic variation, which will have been computed
    previously based on the magnetic declanation at the aricraft's location.
    """
    units = 'deg'
    
    def derive(self, head_true=P('Heading True Continuous'),
               mag_var=P('Magnetic Variation')):
        self.array = (head_true.array - mag_var.array) % 360.0


class HeadingTrue(DerivedParameterNode):
    """
    Compensates for magnetic variation, which will have been computed
    previously.
    
    The Magnetic Variation from identified Takeoff and Landing runways is
    taken in preference to that calculated based on geographical latitude and
    longitude in order to account for any compass drift or out of date
    magnetic variation databases on the aircraft.
    """
    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return 'Heading Continuous' in available and \
               any_of(('Magnetic Variation From Runway', 'Magnetic Variation'),
                      available)
        
    def derive(self, head=P('Heading Continuous'),
               rwy_var=P('Magnetic Variation From Runway'),
               mag_var=P('Magnetic Variation')):
        if rwy_var and np.ma.count(rwy_var.array):
            # use this in preference
            var = rwy_var.array
        else:
            var = mag_var.array
        self.array = (head.array + var) % 360.0


class ILSFrequency(DerivedParameterNode):
    """
    This code is based upon the normal operation of an Instrument Landing
    System whereby the left and right receivers are tuned to the same runway
    ILS frequency. This allows independent monitoring of the approach by the
    two crew.

    If there is a problem with the system, users can inspect the (1) and (2)
    signals separately, although the normal use will show valid ILS data when
    both are tuned to the same frequency.
    """

    name = "ILS Frequency"
    units='MHz'
    align = False

    @classmethod
    def can_operate(cls, available):
        return ('ILS (1) Frequency' in available and
                'ILS (2) Frequency' in available) or \
               ('ILS-VOR (1) Frequency' in available)
    
    def derive(self, f1=P('ILS (1) Frequency'),f2=P('ILS (2) Frequency'),
               f1v=P('ILS-VOR (1) Frequency'), f2v=P('ILS-VOR (2) Frequency')):
                

        #TODO: Extend to allow for three-receiver installations


        if f1 and f2:
            first = f1.array
            second = f2.array
        else:
            if f1v and f2v==None:
                # Some aircraft have inoperative ILS-VOR (2) systems, which
                # record frequencies outside the valid range.
                first = f1v.array
            elif f1v and f2v:
                first = f1v.array
                second = f2v.array
            else:
                raise "Unrecognised set of ILS frequency parameters"

        # Mask invalid frequencies
        f1_trim = filter_vor_ils_frequencies(first, 'ILS')
        if f1v and f2v==None:
            mask = first.mask
        else:
            # We look for both
            # receivers being tuned together to form a valid signal
            f2_trim = filter_vor_ils_frequencies(second, 'ILS')
            # and mask where the two receivers are not matched
            mask = np.ma.masked_not_equal(f1_trim - f2_trim, 0.0).mask

        self.array = np.ma.array(data=f1_trim.data, mask=mask)


class ILSLocalizer(DerivedParameterNode):

    # List the minimum acceptable parameters here
    @classmethod
    def can_operate(cls, available):
        return any_of(('ILS (1) Localizer', 'ILS (2) Localizer'), available)\
               or\
               any_of(('ILS Localizer (Capt)', 'ILS Localizer (Azimuth)'), available)

    name = "ILS Localizer"
    units = 'dots'
    align = False

    def derive(self, loc_1=P('ILS (1) Localizer'),loc_2=P('ILS (2) Localizer'),
               loc_c=P('ILS Localizer (Capt)'),loc_az=P('ILS Localizer (Azimuth)')):
        if loc_1 or loc_2:
            self.array, self.frequency, self.offset = blend_two_parameters(loc_1, loc_2)
        else:
            self.array, self.frequency, self.offset = blend_two_parameters(loc_c, loc_az)


class ILSGlideslope(DerivedParameterNode):

    """
    This derived parameter merges the available sources into a single
    consolidated parameter. The more complex form of parameter blending is
    used to allow for many permutations.
    """

    name = "ILS Glideslope"
    units = 'dots'
    align = False

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)
    
    def derive(self,
               source_A=P('ILS (1) Glideslope'),
               source_B=P('ILS (2) Glideslope'),
               source_C=P('ILS (3) Glideslope'),
               
               source_E=P('ILS (L) Glideslope'),
               source_F=P('ILS (R) Glideslope'),
               source_G=P('ILS (C) Glideslope'),
               
               source_J=P('ILS (EFIS) Glideslope'),

               source_M=P('ILS Glideslope (Capt)'),
               source_N=P('ILS Glideslope (FO)'),
               ):
        sources = [source_A, source_B, source_C,
                   source_E, source_F, source_G,
                   source_J,
                   source_M, source_N
                   ]
        self.offset = 0.0
        self.frequency = 2.0
        self.array = blend_parameters(sources, 
                                      offset=self.offset, 
                                      frequency=self.frequency,
                                      )


class AimingPointRange(DerivedParameterNode):
    """
    Aiming Point Range is derived from the Approach Range. The units are
    converted to nautical miles ready for plotting and the datum is offset to
    either the ILS Glideslope Antenna position where an ILS is installed or
    the nominal threshold position where there is no ILS installation.
    """

    units = 'nm'

    def derive(self, app_rng=P('Approach Range'),
               approaches=App('Approach Information'),
               ):
        self.array = np_ma_masked_zeros_like(app_rng.array)

        for approach in approaches:
            runway = approach.runway
            if not runway:
                # no runway to establish distance to glideslope antenna
                continue
            try:
                extend = runway_distances(runway)[1] # gs_2_loc
            except (KeyError, TypeError):
                extend = runway_length(runway) - 1000 / METRES_TO_FEET

            s = approach.slice
            self.array[s] = (app_rng.array[s] - extend) / METRES_TO_NM


class CoordinatesSmoothed(object):
    '''
    Superclass for SmoothedLatitude and SmoothedLongitude classes as they share
    the adjust_track methods.

    _adjust_track_pp is used for aircraft with precise positioning, usually
    GPS based and qualitatively determined by a recorded track that puts the
    aircraft on the correct runway. In these cases we only apply fine
    adjustment of the approach and landing path using ILS localizer data to
    position the aircraft with respect to the runway centreline.

    _adjust_track_ip is for aircraft with imprecise positioning. In these
    cases we use all the data available to correct for errors in the recorded
    position at takeoff, approach and landing.
    '''
    def taxi_out_track_pp(self, lat, lon, speed, hdg, freq):
        '''
        Compute a groundspeed and heading based taxi out track.
        '''

        lat_out, lon_out, wt = ground_track_precise(lat, lon, speed, hdg,
                                                    freq, 'takeoff')
        return lat_out, lon_out

    def taxi_in_track_pp(self, lat, lon, speed, hdg, freq):
        '''
        Compute a groundspeed and heading based taxi in track.
        '''
        lat_in, lon_in, wt = ground_track_precise(lat, lon, speed, hdg, freq,
                                              'landing')
        return lat_in, lon_in

    def taxi_out_track(self, toff_slice, lat_adj, lon_adj, speed, hdg, freq):
        '''
        Compute a groundspeed and heading based taxi out track.
        TODO: Include lat & lon corrections for precise positioning tracks.
        '''
        lat_out, lon_out = \
            ground_track(lat_adj[toff_slice.start],
                         lon_adj[toff_slice.start],
                         speed[:toff_slice.start],
                         hdg.array[:toff_slice.start],
                         freq,
                         'takeoff')
        return lat_out, lon_out

    def taxi_in_track(self, lat_adj, lon_adj, speed, hdg, freq):
        '''
        Compute a groundspeed and heading based taxi in track.
        '''
        if len(speed):
            lat_in, lon_in = ground_track(lat_adj[0],
                                          lon_adj[0],
                                          speed,
                                          hdg,
                                          freq,
                                          'landing')
            return lat_in, lon_in
        else:
            return [],[]

    def _adjust_track(self, lon, lat, ils_loc, app_range, hdg, gspd, tas,
                      toff, toff_rwy, tdwns, approaches, mobile, precise):
        '''
        Returns track adjustment 
        '''
        # Set up a working space.
        lat_adj = np_ma_masked_zeros_like(hdg.array)
        lon_adj = np_ma_masked_zeros_like(hdg.array)

        mobiles = [s.slice for s in mobile]
        begin = mobiles[0].start
        end = mobiles[-1].stop

        ils_join_offset = None

        #------------------------------------
        # Use synthesized track for takeoffs
        #------------------------------------

        # We compute the ground track using best available data.
        if gspd:
            speed = gspd.array
            freq = gspd.frequency
        else:
            speed = tas.array
            freq = tas.frequency

        try:
            toff_slice = toff[0].slice
        except:
            toff_slice = None

        if toff_slice and precise:
            try:
                lat_out, lon_out = self.taxi_out_track_pp(
                    lat.array[begin:toff_slice.start],
                    lon.array[begin:toff_slice.start],
                    speed[begin:toff_slice.start],
                    hdg.array[begin:toff_slice.start],
                    freq)
            except ValueError:
                self.exception("'%s'. Using non smoothed coordinates for Taxi Out",
                             self.__class__.__name__)
                lat_out = lat.array[begin:toff_slice.start]
                lon_out = lon.array[begin:toff_slice.start]
            lat_adj[begin:toff_slice.start] = lat_out
            lon_adj[begin:toff_slice.start] = lon_out

        elif toff_slice and toff_rwy and toff_rwy.value:

            start_locn_recorded = runway_snap_dict(
                toff_rwy.value,lat.array[toff_slice.start],
                lon.array[toff_slice.start])
            start_locn_default = toff_rwy.value['start']
            _,distance = bearing_and_distance(start_locn_recorded['latitude'],
                                              start_locn_recorded['longitude'],
                                              start_locn_default['latitude'],
                                              start_locn_default['longitude'])

            if distance < 50:
                # We may have a reasonable start location, so let's use that
                start_locn = start_locn_recorded
                initial_displacement = 0.0
            else:
                # The recorded start point is way off, default to 50m down the track.
                start_locn = start_locn_default
                initial_displacement = 50.0
            
            # With imprecise navigation options it is common for the lowest
            # speeds to be masked, so we pretend to accelerate smoothly from
            # standstill.
            if speed[toff_slice][0] is np.ma.masked:
                speed.data[toff_slice][0] = 0.0
                speed.mask[toff_slice][0]=False
                speed[toff_slice] = interpolate(speed[toff_slice])

            # Compute takeoff track from start of runway using integrated
            # groundspeed, down runway centreline to end of takeoff (35ft
            # altitude). An initial value of 100m puts the aircraft at a
            # reasonable position with respect to the runway start.
            rwy_dist = np.ma.array(
                data = integrate(speed[toff_slice], freq,
                                 initial_value=initial_displacement,
                                 scale=KTS_TO_MPS),
                mask = np.ma.getmaskarray(speed[toff_slice]))

            # Similarly the runway bearing is derived from the runway endpoints
            # (this gives better visualisation images than relying upon the
            # nominal runway heading). This is converted to a numpy masked array
            # of the length required to cover the takeoff phase.
            rwy_hdg = runway_heading(toff_rwy.value)
            rwy_brg = np_ma_ones_like(speed[toff_slice])*rwy_hdg

            # The track down the runway centreline is then converted to
            # latitude and longitude.
            lat_adj[toff_slice], lon_adj[toff_slice] = \
                latitudes_and_longitudes(rwy_brg,
                                         rwy_dist,
                                         start_locn)

            lat_out, lon_out = self.taxi_out_track(toff_slice, lat_adj, lon_adj, speed, hdg, freq)

            # If we have an array holding the taxi out track, then we use
            # this, otherwise we hold at the startpoint.
            if lat_out is not None and lat_out.size:
                lat_adj[:toff_slice.start] = lat_out
            else:
                lat_adj[:toff_slice.start] = lat_adj[toff_slice.start]

            if lon_out is not None and lon_out.size:
                lon_adj[:toff_slice.start] = lon_out
            else:
                lon_adj[:toff_slice.start] = lon_adj[toff_slice.start]

        else:
            print 'Cannot smooth taxi out'

        #-----------------------------------------------------------------------
        # Use ILS track for approach and landings in all localizer approches
        #-----------------------------------------------------------------------

        for approach in approaches:

            this_app_slice = approach.slice

            runway = approach.runway
            if not runway:
                continue

            if approach.loc_est:
                this_loc_slice = approach.loc_est

                # Adjust the ils data to be degrees from the reference point.
                scale = localizer_scale(runway)
                bearings = ils_loc.array[this_loc_slice] * scale + \
                    runway_heading(runway)+180

                if precise:

                    # Tweek the localizer position to be on the start:end centreline
                    localizer_on_cl = ils_localizer_align(runway)

                    # Find distances from the localizer
                    _, distances = bearings_and_distances(lat.array[this_loc_slice],
                                                          lon.array[this_loc_slice],
                                                          localizer_on_cl)


                    # At last, the conversion of ILS localizer data to latitude and longitude
                    lat_adj[this_loc_slice], lon_adj[this_loc_slice] = \
                        latitudes_and_longitudes(bearings, distances, localizer_on_cl)

                else: # Imprecise navigation but with an ILS tuned.

                    # Adjust distance units
                    distances = app_range.array[this_loc_slice]

                    ## This test was introduced as a  precaution against poor 
                    ## quality data, but in fact for landings where only airspeed 
                    ## data is available, none of the data below 60kt will be valid, 
                    ## hence this test was removed.
                    ##if np.ma.count(distances)/float(len(distances)) < 0.8:
                        ##continue # Insufficient range data to make this worth computing.

                    # Tweek the localizer position to be on the start:end centreline
                    localizer_on_cl = ils_localizer_align(runway)

                    # At last, the conversion of ILS localizer data to latitude and longitude
                    lat_adj[this_loc_slice], lon_adj[this_loc_slice] = \
                        latitudes_and_longitudes(bearings, distances,
                                                 localizer_on_cl)

                # Alignment of the ILS Localizer Range causes corrupt first
                # samples.
                lat_adj[this_loc_slice.start] = np.ma.masked
                lon_adj[this_loc_slice.start] = np.ma.masked

                ils_join_offset = None
                if approach.type == 'LANDING':
                    # Remember where we lost the ILS, in preparation for the taxi in.
                    ils_join, _ = last_valid_sample(lat_adj[this_loc_slice])
                    if ils_join:
                        ils_join_offset = this_loc_slice.start + ils_join

            else:
                # No localizer in this approach

                if precise:
                    # Without an ILS we can do no better than copy the prepared arrray data forwards.
                    lat_adj[this_app_slice] = lat.array[this_app_slice]
                    lon_adj[this_app_slice] = lon.array[this_app_slice]
                else:
                    '''
                    We need to fix the bottom end of the descent without an
                    ILS to fix. The best we can do is put the touchdown point
                    in the right place. (An earlier version put the track
                    onto the runway centreline which looked convincing, but
                    went disasterously wrong for curving visual approaches
                    into airfields like Nice).
                    '''
                    # Q: Currently we rely on a Touchdown KTI existing to smooth
                    #    a track without the ILS Localiser being established or
                    #    precise positioning. This is to ensure that the
                    #    aircraft is on the runway and therefore we can use
                    #    database coordinates for the runway to smooth the
                    #    track. This does not provide a solution for aircraft
                    #    which do not momentarily land on the runway. Could we
                    #    assume that the aircraft will match the runway
                    #    coordinates if it drops below a certain altitude as
                    #    this will be more accurate than low precision
                    #    positioning equipment.
                    for tdwn in tdwns:
                        if not is_index_within_slice(tdwn.index, this_app_slice):
                            continue

                        # Adjust distance units
                        distance = np.ma.array([value_at_index(app_range.array, tdwn.index)])
                        bearing = np.ma.array([(runway_heading(runway)+180)%360.0])
                        # Reference point for visual approaches is the runway end.
                        ref_point = runway['end']

                        # Work out the touchdown point
                        lat_tdwn, lon_tdwn = latitudes_and_longitudes \
                            (bearing, distance, ref_point)

                        lat_err = value_at_index(lat.array, tdwn.index) - lat_tdwn
                        lon_err = value_at_index(lon.array, tdwn.index) - lon_tdwn
                        lat_adj[this_app_slice] = lat.array[this_app_slice] - lat_err
                        lon_adj[this_app_slice] = lon.array[this_app_slice] - lon_err

            # The computation of a ground track is not ILS dependent and does
            # not depend upon knowing the runway details.
            if approach.type == 'LANDING':
                # This function returns the lowest non-None offset.
                join_idx = min(filter(bool, [ils_join_offset,
                                             approach.turnoff]))

                if join_idx and (len(lat_adj) > join_idx): # We have some room to extend over.

                    if precise:
                        # Set up the point of handover
                        lat.array[join_idx] = lat_adj[join_idx]
                        lon.array[join_idx] = lon_adj[join_idx]
                        try:
                            lat_in, lon_in = self.taxi_in_track_pp(
                                lat.array[join_idx:end],
                                lon.array[join_idx:end],
                                speed[join_idx:end],
                                hdg.array[join_idx:end],
                                freq)
                        except ValueError:
                            self.exception("'%s'. Using non smoothed coordinates for Taxi In",
                                           self.__class__.__name__)
                            lat_in = lat.array[join_idx:end]
                            lon_in = lon.array[join_idx:end]
                    else:
                        if join_idx and (len(lat_adj) > join_idx):
                            scan_back = slice(join_idx, this_app_slice.start, -1)
                            lat_join = first_valid_sample(lat_adj[scan_back])
                            lon_join = first_valid_sample(lon_adj[scan_back])
                            join_idx -= max(lat_join.index, lon_join.index) # step back to make sure the join location is not masked.
                            lat_in, lon_in = self.taxi_in_track(
                                lat_adj[join_idx:end],
                                lon_adj[join_idx:end],
                                speed[join_idx:end],
                                hdg.array[join_idx:end],
                                freq,
                            )

                    # If we have an array of taxi in track values, we use
                    # this, otherwise we hold at the end of the landing.
                    if lat_in is not None and np.ma.count(lat_in):
                        lat_adj[join_idx:end] = lat_in
                    else:
                        lat_adj[join_idx:end] = lat_adj[join_idx]
                        
                    if lon_in is not None and np.ma.count(lon_in):
                        lon_adj[join_idx:end] = lon_in
                    else:
                        lon_adj[join_idx:end] = lon_adj[join_idx]

        return lat_adj, lon_adj


class LatitudeSmoothed(DerivedParameterNode, CoordinatesSmoothed):
    """
    From a prepared Latitude parameter, which may have been created by
    straightening out a recorded latitude data set, or from an estimate using
    heading and true airspeed, we now match the data to the available runway
    data. (Airspeed is included as an alternative to groundspeed so that the
    algorithm has wider applicability).

    Where possible we use ILS data to make the landing data as accurate as
    possible, and we create ground track data with groundspeed and heading if
    available.

    Once these sections have been created, the parts are 'stitched' together
    to make a complete latitude trace.

    The first parameter in the derive method is heading_continuous, which is
    always available and which should always have a sample rate of 1Hz. This
    ensures that the resulting computations yield a smoothed track with 1Hz
    spacing, even if the recorded latitude and longitude have only 0.25Hz
    sample rate.
    """

    # List the minimum acceptable parameters here
    @classmethod
    def can_operate(cls, available):
        return all_of((
            'Latitude Prepared',
            'Longitude Prepared',
            'Approach Range',
            'Airspeed True',
            'Precise Positioning',
            'Takeoff',
            'FDR Takeoff Runway',
            'Touchdown',
            'Approach Information',
            'Mobile'), available) \
               and any_of(('Heading True Continuous',
                           'Heading Continuous'), available)

    units = 'deg'

    def derive(self, lat=P('Latitude Prepared'),
               lon=P('Longitude Prepared'),
               hdg_mag=P('Heading Continuous'),
               ils_loc=P('ILS Localizer'),
               app_range=P('Approach Range'),
               hdg_true=P('Heading True Continuous'),
               gspd=P('Groundspeed'),
               tas=P('Airspeed True'),
               precise=A('Precise Positioning'),
               toff=S('Takeoff'),
               toff_rwy = A('FDR Takeoff Runway'),
               tdwns = S('Touchdown'),
               approaches = App('Approach Information'),
               mobile=S('Mobile'),
               ):
        precision = bool(getattr(precise, 'value', False))

        if hdg_true:
            hdg = hdg_true
        else:
            hdg = hdg_mag

        lat_adj, lon_adj = self._adjust_track(
            lon, lat, ils_loc, app_range, hdg, gspd, tas, toff, toff_rwy, tdwns,
            approaches, mobile, precision)
        self.array = track_linking(lat.array, lat_adj)


class LongitudeSmoothed(DerivedParameterNode, CoordinatesSmoothed):
    """
    See Latitude Smoothed for notes.
    """

    units = 'deg'
    ##align_frequency = 1.0
    ##align_offset = 0.0

    @classmethod
    def can_operate(cls, available):
        return all_of((
            'Latitude Prepared',
            'Longitude Prepared',
            'Approach Range',
            'Airspeed True',
            'Precise Positioning',
            'Takeoff',
            'FDR Takeoff Runway',
            'Touchdown',
            'Approach Information',
            'Mobile'), available) \
               and any_of(('Heading True Continuous',
                           'Heading Continuous'), available)

    def derive(self, lat = P('Latitude Prepared'),
               lon = P('Longitude Prepared'),
               hdg_mag=P('Heading Continuous'),
               ils_loc = P('ILS Localizer'),
               app_range = P('Approach Range'),
               hdg_true = P('Heading True Continuous'),
               gspd = P('Groundspeed'),
               tas = P('Airspeed True'),
               precise =A('Precise Positioning'),
               toff = S('Takeoff'),
               toff_rwy = A('FDR Takeoff Runway'),
               tdwns = S('Touchdown'),
               approaches = App('Approach Information'),
               mobile=S('Mobile'),
               ):
        precision = bool(getattr(precise, 'value', False))

        if hdg_true:
            hdg = hdg_true
        else:
            hdg = hdg_mag

        lat_adj, lon_adj = self._adjust_track(lon, lat, ils_loc, app_range, hdg,
                                            gspd, tas, toff, toff_rwy,
                                            tdwns, approaches, mobile, precision)
        self.array = track_linking(lon.array, lon_adj)

class Mach(DerivedParameterNode):
    '''
    Mach derived from air data parameters for aircraft where no suitable Mach
    data is recorded.
    '''

    units = 'Mach'

    def derive(self, cas = P('Airspeed'), alt = P('Altitude STD')):
        dp = cas2dp(cas.array)
        p = alt2press(alt.array)
        self.array = dp_over_p2mach(dp/p)


class MagneticVariation(DerivedParameterNode):
    """
    This computes magnetic declination values from latitude, longitude,
    altitude and date. Uses Latitude/Longitude or
    Latitude (Coarse)/Longitude (Coarse) parameters instead of Prepared or
    Smoothed to avoid cyclical dependencies.
    """

    units = 'deg'
    
    align_frequency = 1/4.0
    align_offset = 0.0

    @classmethod
    def can_operate(cls, available):
        lat = any_of(('Latitude', 'Latitude (Coarse)'), available)
        lon = any_of(('Longitude', 'Longitude (Coarse)'), available)
        return lat and lon and all_of(('Altitude AAL', 'Start Datetime'),
                                      available)

    def derive(self, lat=P('Latitude'), lat_coarse=P('Latitude (Coarse)'),
               lon=P('Longitude'), lon_coarse=P('Longitude (Coarse)'),
               alt_aal=P('Altitude AAL'), start_datetime=A('Start Datetime')):
        
        lat = lat or lat_coarse
        lon = lon or lon_coarse
        mag_var_frequency = 64 * self.frequency
        mag_vars = []
        start_date = start_datetime.value.date()
        # TODO: Optimize.
        for lat_val, lon_val, alt_aal_val in zip(lat.array[::mag_var_frequency],
                                                 lon.array[::mag_var_frequency],
                                                 alt_aal.array[::mag_var_frequency]):
            if np.ma.masked in (lat_val, lon_val, alt_aal_val):
                mag_vars.append(np.ma.masked)
            else:
                mag_vars.append(geomag.declination(lat_val, lon_val,
                                                   alt_aal_val,
                                                   time=start_date))
        
        # Repair mask to avoid interpolating between masked values.
        mag_vars = repair_mask(np.ma.array(mag_vars), extrapolate=True)
        interpolator = interp1d(
            np.arange(0, len(lat.array), mag_var_frequency), mag_vars)
        interpolation_length = (len(mag_vars) - 1) * mag_var_frequency
        array = np_ma_masked_zeros_like(lat.array)
        array[:interpolation_length] = \
            interpolator(np.arange(interpolation_length))
        
        # Exclude masked values.
        mask = lat.array.mask | lon.array.mask | alt_aal.array.mask
        array = np.ma.masked_where(mask, array)
        self.array = repair_mask(array, extrapolate=True,
                                 repair_duration=None)


class MagneticVariationFromRunway(DerivedParameterNode):
    """
    This computes local magnetic variation values on the runways and
    interpolates between one airport and the next. The values at each airport
    are kept constant.
    
    Runways identified by approaches are not included as the aircraft may
    have drift and therefore cannot establish the heading of the runway as it
    does not land on it.

    The main idea here is that we can easily identify the ends of the runway
    and the heading of the aircraft on the runway. This allows a Heading True
    to be derived from the aircraft's perceived magnetic variation. This is
    important as some aircraft's recorded Heading (magnetic) can be based
    upon magnetic variation from out of date databases. Also, by using the
    aircraft compass values to work out the variation, we inherently
    accommodate compass drift for that day.
    
    TODO: Instead of linear interpolation, perhaps base it on distance flown.
    """
    units = 'deg'
    align_frequency = 1/4.0
    align_offset = 0.0

    def derive(self, duration=A('HDF Duration'),
               head_toff = KPV('Heading During Takeoff'),
               head_land = KPV('Heading During Landing'),
               toff_rwy = A('FDR Takeoff Runway'),
               land_rwy = A('FDR Landing Runway')):
        array_len = duration.value * self.frequency
        dev = np.ma.zeros(array_len)
        dev.mask = True
        
        # takeoff
        tof_hdg_mag_kpv = head_toff.get_first()
        if tof_hdg_mag_kpv and toff_rwy:
            takeoff_hdg_mag = tof_hdg_mag_kpv.value
            try:
                takeoff_hdg_true = runway_heading(toff_rwy.value)
            except ValueError:
                # runway does not have coordinates to calculate true heading
                pass
            else:
                dev[tof_hdg_mag_kpv.index] = takeoff_hdg_true - takeoff_hdg_mag
        
        # landing
        ldg_hdg_mag_kpv = head_land.get_last()
        if ldg_hdg_mag_kpv and land_rwy:
            landing_hdg_mag = ldg_hdg_mag_kpv.value
            try:
                landing_hdg_true = runway_heading(land_rwy.value)
            except ValueError:
                # runway does not have coordinates to calculate true heading
                pass
            else:
                dev[ldg_hdg_mag_kpv.index] = landing_hdg_true - landing_hdg_mag

        # linearly interpolate between values and extrapolate to ends of the
        # array, even if only the takeoff variation is calculated as the
        # landing variation is more likely to be the same as takeoff than 0
        # degrees (and vice versa).
        self.array = interpolate(dev, extrapolate=True)



class VerticalSpeedInertial(DerivedParameterNode):
    '''
    See 'Vertical Speed' for pressure altitude based derived parameter.
    
    If the aircraft records an inertial vertical speed, rename this "Vertical
    Speed Inertial - Recorded" to avoid conflict

    This routine derives the vertical speed from the vertical acceleration, the
    Pressure altitude and the Radio altitude.

    Long term errors in the accelerometers are removed by washing out the
    acceleration term with a longer time constant filter before use. The
    consequence of this is that long period movements with continued
    acceleration will be underscaled slightly. As an example the test case
    with a 1ft/sec^2 acceleration results in an increasing vertical speed of
    55 fpm/sec, not 60 as would be theoretically predicted.

    Complementary first order filters are used to combine the acceleration
    data and the height data. A high pass filter on the altitude data and a
    low pass filter on the acceleration data combine to form a consolidated
    signal.
    
    See also http://www.flightdatacommunity.com/inertial-smoothing.
    '''

    units = 'fpm'

    def derive(self,
               az = P('Acceleration Vertical'),
               alt_std = P('Altitude STD Smoothed'),
               alt_rad = P('Altitude Radio'),
               fast = S('Fast')):

        def inertial_vertical_speed(alt_std_repair, frequency, alt_rad_repair,
                                    az_repair):
            # Uses the complementary smoothing approach

            # This is the accelerometer washout term, with considerable gain.
            # The initialisation "initial_value=az_repair[0]" is very
            # important, as without this the function produces huge spikes at
            # each start of a data period.
            az_washout = first_order_washout (az_repair,
                                              AZ_WASHOUT_TC, frequency,
                                              gain=GRAVITY_IMPERIAL,
                                              initial_value=np.ma.mean(az_repair[0:40]))
            inertial_roc = first_order_lag (az_washout,
                                            VERTICAL_SPEED_LAG_TC,
                                            frequency,
                                            gain=VERTICAL_SPEED_LAG_TC)

            # We only differentiate the pressure altitude data.
            roc_alt_std = first_order_washout(alt_std_repair,
                                              VERTICAL_SPEED_LAG_TC, frequency,
                                              gain=1/VERTICAL_SPEED_LAG_TC)

            roc = (roc_alt_std + inertial_roc)
            hz = az.frequency
            
            # Between 100ft and the ground, replace the computed data with a
            # purely inertial computation to avoid ground effect.
            climbs = slices_from_to(alt_rad_repair, 0, 100)[1]
            for climb in climbs:
                # From 5 seconds before lift to 100ft
                lift_m5s = max(0, climb.start - 5*hz)
                up = slice(lift_m5s if lift_m5s >= 0 else 0, climb.stop)
                up_slope = integrate(az_washout[up], hz)
                blend_end_error = roc[climb.stop-1] - up_slope[-1]
                blend_slope = np.linspace(0.0, blend_end_error, climb.stop-climb.start)
                roc[:lift_m5s] = 0.0
                roc[lift_m5s:climb.start] = up_slope[:climb.start-lift_m5s]
                roc[climb] = up_slope[climb.start-lift_m5s:] + blend_slope
                '''
                # Debug plot only.
                import matplotlib.pyplot as plt
                plt.plot(az_washout[up],'k')
                plt.plot(up_slope, 'g')
                plt.plot(roc[up],'r')
                plt.plot(alt_rad_repair[up], 'c')
                plt.show()
                plt.clf()
                plt.close()
                '''
                
            descents = slices_from_to(alt_rad_repair, 100, 0)[1]
            for descent in descents:
                down = slice(descent.start, descent.stop+5*hz)
                down_slope = integrate(az_washout[down], 
                                       hz,)
                blend = roc[down.start] - down_slope[0]
                blend_slope = np.linspace(blend, -down_slope[-1], len(down_slope))
                roc[down] = down_slope + blend_slope
                roc[descent.stop+5*hz:] = 0.0
                '''
                # Debug plot only.
                import matplotlib.pyplot as plt
                plt.plot(az_washout[down],'k')
                plt.plot(down_slope,'g')
                plt.plot(roc[down],'r')
                plt.plot(blend_slope,'b')
                plt.plot(down_slope + blend_slope,'m')
                plt.plot(alt_rad_repair[down], 'c')
                plt.show()
                plt.close()
                '''

            return roc * 60.0

        # Make space for the answers
        self.array = np_ma_masked_zeros_like(alt_std.array)
        hz = az.frequency
        
        for speedy in fast:
            # Fix minor dropouts
            az_repair = repair_mask(az.array[speedy.slice], 
                                    frequency=hz)
            alt_rad_repair = repair_mask(alt_rad.array[speedy.slice], 
                                         frequency=hz,
                                         repair_duration=None)
            alt_std_repair = repair_mask(alt_std.array[speedy.slice], 
                                         frequency=hz)
    
            # np.ma.getmaskarray ensures we have complete mask arrays even if
            # none of the samples are masked (normally returns a single
            # "False" value. We ignore the rad alt mask because we are only
            # going to use the radio altimeter values below 100ft, and short
            # transients will have been repaired. By repairing with the
            # repair_duration=None option, we ignore the masked saturated
            # values at high altitude.
    
            az_masked = np.ma.array(data = az_repair.data,
                                    mask = np.ma.logical_or(
                                        np.ma.getmaskarray(az_repair),
                                        np.ma.getmaskarray(alt_std_repair)))
    
            # We are going to compute the answers only for ranges where all
            # the required parameters are available.
            clumps = np.ma.clump_unmasked(az_masked)
            for clump in clumps:
                self.array[shift_slice(clump,speedy.slice.start)] = inertial_vertical_speed(
                    alt_std_repair[clump], az.frequency,
                    alt_rad_repair[clump], az_repair[clump])



class VerticalSpeed(DerivedParameterNode):
    '''
    The period for averaging altitude data is a trade-off between transient
    response and noise rejection.

    Some older aircraft have poor resolution, and the 4 second timebase
    leaves a noisy signal. We have inspected Hercules data, where the
    resolution is of the order of 9 ft/bit, and data from the BAe 146 where
    the resolution is 15ft and 737-6 frames with 32ft resolution. In these
    cases the wider timebase with greater smoothing is necessary, albeit at
    the expense of transient response.

    For most aircraft however, a period of 4 seconds is used. This has been
    found to give good results, and is also the value used to compute the
    recorded Vertical Speed parameter on Airbus A320 series aircraft
    (although in that case the data is delayed, and the aircraft cannot know
    the future altitudes!).
    '''

    units = 'fpm'

    @classmethod
    def can_operate(cls, available):
        if 'Altitude STD Smoothed' in available:
            return True

    def derive(self, alt_std=P('Altitude STD Smoothed'), frame=A('Frame')):
        frame_name = frame.value if frame else ''

        if frame_name in ['146'] or \
           frame_name.startswith('747-200') or \
           frame_name.startswith('737-6'):
            self.array = rate_of_change(alt_std, 11.0) * 60.0
        elif frame_name in ['L382-Hercules']:
             self.array = rate_of_change(alt_std, 15.0, method='regression') * 60.0
        else:
            self.array = rate_of_change(alt_std, 4.0) * 60.0


class VerticalSpeedForFlightPhases(DerivedParameterNode):
    """
    A simple and robust vertical speed parameter suitable for identifying
    flight phases. DO NOT use this for event detection.
    """

    units = 'fpm'

    def derive(self, alt_std = P('Altitude STD Smoothed')):
        # This uses a scaled hysteresis parameter. See settings for more detail.
        threshold = HYSTERESIS_FPROC * max(1, rms_noise(alt_std.array))
        # The max(1, prevents =0 case when testing with artificial data.
        self.array = hysteresis(rate_of_change(alt_std, 6) * 60, threshold)


class Relief(DerivedParameterNode):
    """
    Also known as Terrain, this is zero at the airfields. There is a small
    cliff in mid-flight where the Altitude AAL changes from one reference to
    another, however this normally arises where Altitude Radio is out of its
    operational range, so will be masked from view.
    """

    units = 'ft'

    def derive(self, alt_aal = P('Altitude AAL'),
               alt_rad = P('Altitude Radio')):
        self.array = alt_aal.array - alt_rad.array


class CoordinatesStraighten(object):
    '''
    Superclass for LatitudePrepared and LongitudePrepared.
    '''
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
        coord1_s = coord1.array
        coord2_s = coord2.array

        # Join the masks, so that we only consider positional data when both are valid:
        coord1_s.mask = np.ma.logical_or(np.ma.getmaskarray(coord1.array),
                                         np.ma.getmaskarray(coord2.array))
        coord2_s.mask = np.ma.getmaskarray(coord1_s)
        # Preload the output with masked values to keep dimension correct
        array = np_ma_masked_zeros_like(coord1_s)

        # Now we just smooth the valid sections.
        tracks = np.ma.clump_unmasked(coord1_s)
        for track in tracks:
            # Reject any data with invariant positions, i.e. sitting on stand.
            if np.ma.ptp(coord1_s[track])>0.0 and np.ma.ptp(coord2_s[track])>0.0:
                coord1_s_track, coord2_s_track, cost = \
                    smooth_track(coord1_s[track], coord2_s[track], coord1.frequency)
                array[track] = coord1_s_track
        return array


class LongitudePrepared(DerivedParameterNode, CoordinatesStraighten):
    """
    See Latitude Smoothed for notes.
    """

    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        return all_of(('Latitude', 'Longitude'), available) or\
               (all_of(('Airspeed True',
                        'Latitude At Liftoff',
                        'Longitude At Liftoff',
                        'Latitude At Touchdown',
                        'Longitude At Touchdown'), available) and\
                any_of(('Heading', 'Heading True'), available))

    # Note hdg is alignment master to force 1Hz operation when latitude &
    # longitude are only recorded at 0.25Hz.
    def derive(self,
               lat=P('Latitude'), lon=P('Longitude'),
               hdg_mag=P('Heading'),
               hdg_true=P('Heading True'),
               tas=P('Airspeed True'),
               lat_lift=KPV('Latitude At Liftoff'),
               lon_lift=KPV('Longitude At Liftoff'),
               lat_land=KPV('Latitude At Touchdown'),
               lon_land=KPV('Longitude At Touchdown')):

        if lat and lon:
            """
            This removes the jumps in longitude arising from the poor resolution of
            the recorded signal.
            """
            self.array = self._smooth_coordinates(lon, lat)
        else:
            if hdg_true:
                hdg = hdg_true
            else:
                hdg = hdg_mag
            _, lon_array = air_track(
                lat_lift.get_first().value, lon_lift.get_first().value,
                lat_land.get_last().value, lon_land.get_last().value,
                tas.array, hdg.array, tas.frequency)
            self.array = lon_array

class LatitudePrepared(DerivedParameterNode, CoordinatesStraighten):
    """
    See Latitude Smoothed for notes.
    """

    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        return all_of(('Latitude', 'Longitude'), available) or \
               (all_of(('Airspeed True',
                        'Latitude At Liftoff',
                        'Longitude At Liftoff',
                        'Latitude At Touchdown',
                        'Longitude At Touchdown'), available) and \
                any_of(('Heading', 'Heading True'), available))

    # Note hdg is alignment master to force 1Hz operation when latitude &
    # longitude are only recorded at 0.25Hz.
    def derive(self,
               lat=P('Latitude'), lon=P('Longitude'),
               hdg_mag=P('Heading'),
               hdg_true=P('Heading True'),
               tas=P('Airspeed True'),
               lat_lift=KPV('Latitude At Liftoff'),
               lon_lift=KPV('Longitude At Liftoff'),
               lat_land=KPV('Latitude At Touchdown'),
               lon_land=KPV('Longitude At Touchdown')):

        if lat and lon:
            self.array = self._smooth_coordinates(lat, lon)
        else:
            if hdg_true:
                hdg = hdg_true
            else:
                hdg = hdg_mag
            lat_array, _ = air_track(
                lat_lift.get_first().value, lon_lift.get_first().value,
                lat_land.get_last().value, lon_land.get_last().value,
                tas.array, hdg.array, tas.frequency)
            self.array = lat_array


class RateOfTurn(DerivedParameterNode):
    """
    Simple rate of change of heading.
    """

    units = 'deg/sec'

    def derive(self, head=P('Heading Continuous')):
        # add a little hysteresis to the rate of change to smooth out minor
        # changes
        roc = rate_of_change(head, 4)
        self.array = hysteresis(roc, 0.1)
        # trouble is that we're loosing the nice 0 values, so force include!
        self.array[(self.array <= 0.05) & (self.array >= -0.05)] = 0


class Pitch(DerivedParameterNode):
    """
    Combination of pitch signals from two sources where required.
    """
    units = 'deg'
    align = False
    def derive(self, p1=P('Pitch (1)'), p2=P('Pitch (2)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(p1, p2)


class PitchRate(DerivedParameterNode):
    """
    Computes rate of change of pitch attitude over a two second period.

    Comment: A two second period is used to remove excessive short period
    transients which the pilot could not realistically be asked to control.
    It also means that low sample rate data (some aircraft have 
    pitch sampled at 1Hz) will still give comparable results. The drawback is
    that very brief transients, for example due to rough handling or
    turbulence, will not be detected.
    
    The rate_of_change algorithm was extended to allow regression
    calculation. This provides a best fit slope over the two second period,
    and so reduces the sensitivity to single samples, but tends to increase
    the peak values. As this also makes the resulting computation suffer more
    from masked values, and increases the computing load, it was decided not
    to implement this for pitch and roll rates.
    
    http://www.flightdatacommunity.com/calculating-pitch-rate/
    """

    units = 'deg/sec'

    def derive(self, pitch=P('Pitch'), frame=A('Frame')):
        frame_name = frame.value if frame else ''
        
        if frame_name in ['L382-Hercules']:
            self.array = rate_of_change(pitch, 8.0, method='regression')
        else:
            # See http://www.flightdatacommunity.com/blog/ for commentary on pitch rate techniques.
            self.array = rate_of_change(pitch, 2.0)


class Roll(DerivedParameterNode):
    """
    Combination of roll signals from two sources where required.
    """
    @classmethod
    def can_operate(cls, available):
        return 'Heading Continuous' in available
    
    units = 'deg'
    align = False
    
    def derive(self, r1=P('Roll (1)'), r2=P('Roll (2)'), 
               hdg=P('Heading Continuous'), frame=A('Frame')):
        frame_name = frame.value if frame else ''
        
        if r1 and r2:
            # Merge data from two sources.
            self.array, self.frequency, self.offset = \
                blend_two_parameters(r1, r2)
        
        elif frame_name in ['L382-Hercules', '1900D-SS542A']:
            # Added Beechcraft as had inoperable Roll
            # Many Hercules aircraft do not have roll recorded. This is a
            # simple substitute, derived from examination of the roll vs
            # heading rate of aircraft with a roll sensor.
            self.array = 6.0 * rate_of_change(hdg, 12.0, method='regression')
            self.frequency = hdg.frequency
            self.offset = hdg.offset

        else:
            raise DataFrameError(self.name, frame_name)


class RollRate(DerivedParameterNode):
    # TODO: Tests.

    '''
    The computational principles here are similar to Pitch Rate; see
    commentary for that parameter.
    '''
    
    units = 'deg/sec'

    def derive(self, roll=P('Roll')):
        self.array = rate_of_change(roll, 2.0)


class RudderPedal(DerivedParameterNode):
    '''
    See Elevator Left for description
    '''
    @classmethod
    def can_operate(cls, available):
        return any_of(('Rudder Pedal Potentiometer', 
                       'Rudder Pedal Synchro'), available)
    
    def derive(self, pot=P('Rudder Pedal Potentiometer'),
               synchro=P('Rudder Pedal Synchro')):

        synchro_samples = 0
        
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
            
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array
        

class ThrottleLevers(DerivedParameterNode):
    """
    A synthetic throttle lever angle, based on the average of the two. Allows
    for simple identification of changes in power etc.
    """

    align = False
    units = 'deg'

    def derive(self,
               tla1=P('Eng (1) Throttle Lever'),
               tla2=P('Eng (2) Throttle Lever')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(tla1, tla2)


class ThrustAsymmetry(DerivedParameterNode):
    '''
    Thrust asymmetry based on N1.

    For EPR rated aircraft, this measure should still be applicable as we are
    not applying a manufacturer's limit to the value, rather this is being
    used to identify imbalance of thrust and as the thrust comes from engine
    speed, N1 is still applicable.

    Using a 5 second moving average to desensitise the parameter against
    transient differences as engines accelerate.

    If we have EPR rated engines, but no N1 recorded, a possible solution
    would be to treat EPR=2.0 as 100% and EPR=1.0 as 0% so the Thrust
    Asymmetry would be simply (EPRmax-EPRmin)*100.

    For propeller aircraft the product of prop speed and torgue should be
    used to provide a similar single asymmetry value.
    '''

    units = '%'

    def derive(self, max_n1=P('Eng (*) N1 Max'), min_n1=P('Eng (*) N1 Min')):
        window = 5 * self.frequency # 5 second window
        self.array = moving_average(max_n1.array - min_n1.array, window=window)


class ThrustReversers(MultistateDerivedParameterNode):
    '''
    A single parameter with multi-state mapping as below.
    '''

    # We are interested in all stowed, all deployed or any other combination.
    # The mapping "In Transit" is used for anything other than the fully
    # established conditions, so for example one locked and the other not is
    # still treated as in transit.
    values_mapping = {
        0: 'Stowed',
        1: 'In Transit',
        2: 'Deployed',
    }

    @classmethod
    def can_operate(cls, available):
        return all_of((
            'Eng (1) Thrust Reverser (L) Deployed',
            'Eng (1) Thrust Reverser (L) Unlocked',
            'Eng (1) Thrust Reverser (R) Deployed',
            'Eng (1) Thrust Reverser (R) Unlocked',
            'Eng (2) Thrust Reverser (L) Deployed',
            'Eng (2) Thrust Reverser (L) Unlocked',
            'Eng (2) Thrust Reverser (R) Deployed',
            'Eng (2) Thrust Reverser (R) Unlocked',
        ), available) or all_of((
            'Eng (1) Thrust Reverser Unlocked',
            'Eng (1) Thrust Reverser Deployed',
            'Eng (2) Thrust Reverser Unlocked',
            'Eng (2) Thrust Reverser Deployed',
        ), available) or all_of((
            'Eng (1) Thrust Reverser In Transit',
            'Eng (1) Thrust Reverser Deployed',
            'Eng (2) Thrust Reverser In Transit',
            'Eng (2) Thrust Reverser Deployed',
        ), available)

    def derive(self,
            e1_dep_all=M('Eng (1) Thrust Reverser Deployed'),
            e1_dep_lft=M('Eng (1) Thrust Reverser (L) Deployed'),
            e1_dep_rgt=M('Eng (1) Thrust Reverser (R) Deployed'),
            e1_ulk_all=M('Eng (1) Thrust Reverser Unlocked'),
            e1_ulk_lft=M('Eng (1) Thrust Reverser (L) Unlocked'),
            e1_ulk_rgt=M('Eng (1) Thrust Reverser (R) Unlocked'),
            e1_tst_all=M('Eng (1) Thrust Reverser In Transit'),
            e2_dep_all=M('Eng (2) Thrust Reverser Deployed'),
            e2_dep_lft=M('Eng (2) Thrust Reverser (L) Deployed'),
            e2_dep_rgt=M('Eng (2) Thrust Reverser (R) Deployed'),
            e2_ulk_all=M('Eng (2) Thrust Reverser Unlocked'),
            e2_ulk_lft=M('Eng (2) Thrust Reverser (L) Unlocked'),
            e2_ulk_rgt=M('Eng (2) Thrust Reverser (R) Unlocked'),
            e2_tst_all=M('Eng (2) Thrust Reverser In Transit'),
            e3_dep_all=M('Eng (3) Thrust Reverser Deployed'),
            e3_dep_lft=M('Eng (3) Thrust Reverser (L) Deployed'),
            e3_dep_rgt=M('Eng (3) Thrust Reverser (R) Deployed'),
            e3_ulk_all=M('Eng (3) Thrust Reverser Unlocked'),
            e3_ulk_lft=M('Eng (3) Thrust Reverser (L) Unlocked'),
            e3_ulk_rgt=M('Eng (3) Thrust Reverser (R) Unlocked'),
            e3_tst_all=M('Eng (3) Thrust Reverser In Transit'),
            e4_dep_all=M('Eng (4) Thrust Reverser Deployed'),
            e4_dep_lft=M('Eng (4) Thrust Reverser (L) Deployed'),
            e4_dep_rgt=M('Eng (4) Thrust Reverser (R) Deployed'),
            e4_ulk_all=M('Eng (4) Thrust Reverser Unlocked'),
            e4_ulk_lft=M('Eng (4) Thrust Reverser (L) Unlocked'),
            e4_ulk_rgt=M('Eng (4) Thrust Reverser (R) Unlocked'),
            e4_tst_all=M('Eng (4) Thrust Reverser In Transit'),):

        deployed_params = (e1_dep_all, e1_dep_lft, e1_dep_rgt, e2_dep_all,
                           e2_dep_lft, e2_dep_rgt, e3_dep_all, e3_dep_lft,
                           e3_dep_rgt, e4_dep_all, e4_dep_lft, e4_dep_rgt)

        deployed_stack = vstack_params_where_state(*[(d, 'Deployed') for d in deployed_params])

        unlocked_params = (e1_ulk_all, e1_ulk_lft, e1_ulk_rgt, e2_ulk_all,
                           e2_ulk_lft, e2_ulk_rgt, e3_ulk_all, e3_ulk_lft,
                           e3_ulk_rgt, e4_ulk_all, e4_ulk_lft, e4_ulk_rgt)

        array = np_ma_zeros_like(deployed_stack[0])
        stacks = [deployed_stack,]

        if any(unlocked_params):
            unlocked_stack = vstack_params_where_state(*[(p, 'Unlocked') for p in unlocked_params])
            array = np.ma.where(unlocked_stack.any(axis=0), 1, array)
            stacks.append(unlocked_stack)

        array = np.ma.where(deployed_stack.any(axis=0), 1, array)
        array = np.ma.where(deployed_stack.all(axis=0), 2, array)
        # update with any transit params
        if any((e1_tst_all, e2_tst_all, e3_tst_all, e4_tst_all)):
            transit_stack = vstack_params_where_state(
                (e1_tst_all, 'In Transit'), (e2_tst_all, 'In Transit'),
                (e3_tst_all, 'In Transit'), (e4_tst_all, 'In Transit'),
            )
            array = np.ma.where(transit_stack.any(axis=0), 1, array)
            stacks.append(transit_stack)

        mask_stack = np.ma.concatenate(stacks, axis=0)

        # mask indexes with greater than 50% masked values
        mask = np.ma.where(mask_stack.mask.sum(axis=0).astype(float)/len(mask_stack)*100 > 50, 1, 0)
        self.array = array
        self.array.mask = mask


class TurbulenceRMSG(DerivedParameterNode):
    """
    Simple RMS g measurement of turbulence over a 5-second period.
    """

    name = 'Turbulence RMS g'
    units = 'RMS g'

    def derive(self, acc=P('Acceleration Vertical')):
        width=int(acc.frequency*5+1)
        mean = moving_average(acc.array, window=width)
        acc_sq = (acc.array)**2.0
        n__sum_sq = moving_average(acc_sq, window=width)
        # Rescaling required as moving average is over width samples, whereas
        # we have only width - 1 gaps; fences and fence posts again !
        core = (n__sum_sq - mean**2.0)*width/(width-1.0)
        self.array = np.ma.sqrt(core)

#------------------------------------------------------------------
# WIND RELATED PARAMETERS
#------------------------------------------------------------------
class WindDirectionContinuous(DerivedParameterNode):
    """
    Like the aircraft heading, this does not jump as it passes through North.
    """
    units = 'deg'
    def derive(self, wind_head=P('Wind Direction')):
        self.array = straighten_headings(wind_head.array)

class WindDirectionTrueContinuous(DerivedParameterNode):
    """
    Like the aircraft heading, this does not jump as it passes through North.
    """
    units = 'deg'
    def derive(self, wind_head=P('Wind Direction True')):
        self.array = straighten_headings(wind_head.array)

class Headwind(DerivedParameterNode):
    """
    This is the headwind, negative values are tailwind.
    """

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        if all_of(('Wind Speed',
                   'Wind Direction Continuous',
                   'Heading True Continuous'), available):
            return True

    def derive(self, windspeed=P('Wind Speed'),
               wind_dir=P('Wind Direction Continuous'),
               head=P('Heading True Continuous'),
               toffs=S('Takeoff'),
               alt_aal=P('Altitude AAL'),
               gspd=P('Groundspeed'),
               aspd=P('Airspeed True')):

        rad_scale = radians(1.0)
        headwind = windspeed.array * np.ma.cos((wind_dir.array-head.array)*rad_scale)

        # If we have airspeed and groundspeed, overwrite the values for the
        # first hundred feet after takeoff. Note this is done in a
        # deliberately crude manner so that the different computations may be
        # identified easily by the analyst.
        if gspd and aspd and alt_aal and toffs:
            # We merge takeoff slices with altitude slices to extend the takeoff phase to 100ft.
            for climb in slices_or(alt_aal.slices_from_to(0, 100),
                                   [s.slice for s in toffs]):
                headwind[climb] = aspd.array[climb] - gspd.array[climb]

        self.array = headwind


class Tailwind(DerivedParameterNode):
    """
    This is the tailwind component.
    """

    units = 'kts'

    def derive(self, hwd=P('Headwind')):
        self.array = -hwd.array


class SAT(DerivedParameterNode):
    """
    Computes Static Air Temperature (temperature of the outside air) from the
    Total Air Temperature, allowing for compressibility effects, or if this
    is not available, the standard atmosphere and lapse rate.
    
    Q: Support transforming SAT from OAT (as they are equal).
    
    TODO: Review naming convention - rename to "Static Air Temperature"?
    """
    @classmethod
    def can_operate(cls, available):
        return True # As Altitude STD must always be available

    name = 'SAT'
    units = 'C'

    def derive(self, tat=P('TAT'), mach=P('Mach'), alt=P('Altitude STD')):
        if tat and mach:
            self.array = machtat2sat(mach.array, tat.array)
        else:
            self.array = alt2sat(alt.array)
    
    
class TAT(DerivedParameterNode):
    """
    Blends data from two air temperature sources.
    
    TODO: Support generation from SAT, Mach and Altitude STD    
    TODO: Review naming convention - rename to "Total Air Temperature"?
    """
    name = "TAT"
    units = 'C'
    align = False

    def derive(self,
               source_1 = P('TAT (1)'),
               source_2 = P('TAT (2)')):

        # Alternate samples (1)&(2) are blended.
        self.array, self.frequency, self.offset = \
            blend_two_parameters(source_1, source_2)


class TAWSAlert(MultistateDerivedParameterNode):
    '''
    Merging all available TAWS alert signals into a single parameter for
    subsequent monitoring.
    '''
    name = 'TAWS Alert'
    values_mapping = {
        0: '-',
        1: 'Alert'}

    @classmethod
    def can_operate(cls, available):
        return any_of(['TAWS Caution Terrain',
                       'TAWS Caution',
                       'TAWS Dont Sink',
                       'TAWS Glideslope'
                       'TAWS Predictive Windshear',
                       'TAWS Pull Up',
                       'TAWS Sink Rate',
                       'TAWS Terrain',
                       'TAWS Terrain Warning Amber',
                       'TAWS Terrain Pull Up',
                       'TAWS Terrain Warning Red',
                       'TAWS Too Low Flap',
                       'TAWS Too Low Gear',
                       'TAWS Too Low Terrain',
                       'TAWS Windshear Warning',
                       ],
                      available)

    def derive(self, airs=S('Airborne'),
               taws_caution_terrain=M('TAWS Caution Terrain'),
               taws_caution=M('TAWS Caution'),
               taws_dont_sink=M('TAWS Dont Sink'),
               taws_glideslope=M('TAWS Glideslope'),
               taws_predictive_windshear=M('TAWS Predictive Windshear'),
               taws_pull_up=M('TAWS Pull Up'),
               taws_sink_rate=M('TAWS Sink Rate'),
               taws_terrain_pull_up=M('TAWS Terrain Pull Up'),
               taws_terrain_warning_amber=M('TAWS Terrain Warning Amber'),
               taws_terrain_warning_red=M('TAWS Terrain Warning Red'),
               taws_terrain=M('TAWS Terrain'),
               taws_too_low_flap=M('TAWS Too Low Flap'),
               taws_too_low_gear=M('TAWS Too Low Gear'),
               taws_too_low_terrain=M('TAWS Too Low Terrain'),
               taws_windshear_warning=M('TAWS Windshear Warning')):

        params_state = vstack_params_where_state(
            (taws_caution_terrain, 'Caution'),
            (taws_caution, 'Caution'),
            (taws_dont_sink, 'Warning'),
            (taws_glideslope, 'Warning'),
            (taws_predictive_windshear, 'Caution'),
            (taws_predictive_windshear, 'Warning'),
            (taws_pull_up, 'Warning'),
            (taws_sink_rate, 'Warning'),
            (taws_terrain_pull_up, 'Warning'),
            (taws_terrain_warning_amber, 'Warning'),
            (taws_terrain_warning_red, 'Warning'),
            (taws_terrain, 'Warning'),
            (taws_too_low_flap, 'Warning'),
            (taws_too_low_gear, 'Warning'),
            (taws_too_low_terrain, 'Warning'),
            (taws_windshear_warning, 'Warning'),
        )
        res = params_state.any(axis=0)

        self.array = np_ma_masked_zeros_like(params_state[0])
        if airs:
            for air in airs:
                self.array[air.slice] = res[air.slice]


class V2(DerivedParameterNode):
    '''
    Derives a value for the V2 velocity speed.

    If no recorded value is available, a value provided in an achieved flight
    record will be used. Alternatively an attempt will be made to determine the
    value from other available parameters.
    '''

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        afr = all_of(('Airspeed', 'AFR V2'), available)
        airbus = all_of(('Airspeed', 'Auto Speed Control', 'Selected Speed'), available)
        return afr or airbus

    def derive(self,
               air_spd=P('Airspeed'),
               afr_v2=A('AFR V2'),
               spd_ctl=P('Auto Speed Control'),
               spd_sel=P('Selected Speed')):

        if afr_v2:
            # Use value supplied in achieved flight record:
            self.array = np_ma_ones_like(air_spd.array) * afr_v2.value
        elif spd_sel:
            # Determine value for some Airbus aircraft:
            self.array = np.ma.where(spd_ctl.array == 1, spd_sel.array, np.ma.masked)
        else:
            # Unable to determine - use zeroed masked array:
            self.array = np_ma_masked_zeros_like(air_spd.array)


class V2Lookup(DerivedParameterNode):
    '''
    Derives a value for the V2 velocity speed looked up from tables.

    The value will be looked up based on weight and flap (surface detents) at
    liftoff. Only the first liftoff will be used.

    Flap is used as the first dependency to avoid interpolation of flap detents
    when flap is recorded at a lower frequency than airspeed.
    '''

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        x = set(available)
        base = ['Airspeed', 'Series', 'Family']
        weight = base + ['Gross Weight At Liftoff']
        airbus = set(weight + ['Configuration']).issubset(x)
        boeing = set(weight + ['Flap']).issubset(x)
        propeller = set(base + ['Eng (*) Np Avg', 'Liftoff']).issubset(x)
        # FIXME: Replace the flaky logic for small propeller aircraft which do
        #        not record gross weight, cannot provide achieved flight
        #        records and will be using a fixed value for processing.
        return airbus or boeing  # or propeller

    def derive(self,
               flap=M('Flap'),
               conf=P('Configuration'),
               air_spd=P('Airspeed'),
               weight_liftoffs=KPV('Gross Weight At Liftoff'),
               series=A('Series'),
               family=A('Family'),
               engine=A('Engine Series'),
               engine_type=A('Engine Type'),
               v2=P('V2'),
               liftoffs=KTI('Liftoff'),
               eng_np=P('Eng (*) Np Avg')):

        # Initialize the result space.
        self.array = np_ma_masked_zeros_like(air_spd.array)

        x = map(lambda x: x.value if x else None, (series, family, engine, engine_type))

        try:
            vspeed_class = get_vspeed_map(*x)
        except KeyError as err:
            if v2:
                self.info("Error in '%s': %s", self.name, err)
            else:
                self.warning("Error in '%s': %s", self.name, err)
            return

        # check Conf as is dependant on Flap
        setting_array = conf.array if conf else flap.array.raw
        vspeed_table = vspeed_class()

        if weight_liftoffs is not None:
            # Explicitly looking for no Gross Weight At Liftoff node, as
            # opposed to having a node with no KPVs
            weight_liftoff = weight_liftoffs.get_first()
            index, weight = weight_liftoff.index, weight_liftoff.value
        else:
            index, weight = liftoffs.get_first().index, None

        setting = setting_array[index]

        try:
            vspeed = vspeed_table.v2(setting, weight)
        except (KeyError, ValueError) as err:
            if v2:
                self.info("Error in '%s': %s", self.name, err)
            else:
                self.warning("Error in '%s': %s", self.name, err)
            # Where the aircraft takes off with flap settings outside the
            # documented V2 range, we need the program to continue without
            # raising an exception, so that the incorrect flap at takeoff
            # can be detected.
            return

        if vspeed is not None:
            self.array[0:] = vspeed
        else:
            self.array[0:] = np.ma.masked


class WindAcrossLandingRunway(DerivedParameterNode):
    """
    This is the windspeed across the final landing runway, positive wind from left to right.
    """
    units = 'kts'
    
    @classmethod
    def can_operate(cls, available):
        return all_of(('Wind Speed', 'Wind Direction True Continuous', 'FDR Landing Runway'), available) \
               or \
               all_of(('Wind Speed', 'Wind Direction Continuous', 'Heading During Landing'), available)

    def derive(self, windspeed=P('Wind Speed'),
               wind_dir_true=P('Wind Direction True Continuous'),
               wind_dir_mag=P('Wind Direction Continuous'),
               land_rwy=A('FDR Landing Runway'),
               land_hdg=KPV('Heading During Landing')):

        if wind_dir_true and land_rwy:
            # proceed with "True" values
            wind_dir = wind_dir_true
            land_heading = runway_heading(land_rwy.value)
            self.array = np_ma_masked_zeros_like(wind_dir_true.array)
        elif wind_dir_mag and land_hdg:
            # proceed with "Magnetic" values
            wind_dir = wind_dir_mag
            land_heading = land_hdg.get_last().value
        else:
            # either no landing runway detected or no landing heading detected
            self.array = np_ma_masked_zeros_like(windspeed.array)
            self.warning('Cannot calculate without landing runway (%s) or landing heading (%s)',
                         bool(land_rwy), bool(land_hdg))
            return
        diff = (land_heading - wind_dir.array) * deg2rad
        self.array = windspeed.array * np.ma.sin(diff)


class Aileron(DerivedParameterNode):
    '''
    Aileron measures the roll control from the Left and Right Aileron
    signals. By taking the average of the two signals, any Flaperon movement
    is removed from the signal, leaving only the difference between the left
    and right which results in the roll control.
    
    Note: This requires that both Aileron signals have positive sign for
    positive (right) rolling moment. That is, port aileron down and starboard
    aileron up have positive sign.
    '''
    align = True
    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        return any_of(('Aileron (L)', 'Aileron (R)'), available)

    def derive(self, al=P('Aileron (L)'), ar=P('Aileron (R)')):
        if al and ar:
            # Taking the average will ensure that positive roll to the right
            # on both signals is maintained as positive control, where as
            # any flaperon action (left positive, right negative) will
            # average out as no roll control.
            self.array = (al.array + ar.array) / 2
        else:
            ail = al or ar
            self.array = ail.array

            
class Flaperon(DerivedParameterNode):
    '''
    Where Ailerons move together and used as Flaps, these are known as
    "Flaperon" control.
    
    Flaperons are measured where both Left and Right Ailerons move down,
    which on the left creates possitive roll but on the right causes negative
    roll. The difference of the two signals is the Flaperon control.
    
    The Flaperon is stepped into nearest aileron detents, e.g. 0, 5, 10 deg
    
    Note: This is used for Airbus models and does not necessarily mean as
    much to other aircraft types.
    '''
    # TODO: Multistate
    def derive(self, al=P('Aileron (L)'), ar=P('Aileron (R)'),
               series=A('Series'), family=A('Family')):
        # Take the difference of the two signals (which should cancel each
        # other out when rolling) and divide the range by two (to account for
        # the left going negative and right going positive when flaperons set)
        flaperon_angle = (al.array - ar.array) / 2
        try:
            ail_steps = get_aileron_map(series.value, family.value)
        except KeyError:
            # no mapping, aircraft must not support Flaperons so create a
            # masked 0 array.
            self.array = None
            return
        else:
            self.array = step_values(flaperon_angle, self.frequency, ail_steps)


class AileronLeft(DerivedParameterNode):
    # See ElevatorLeft for explanation
    name = 'Aileron (L)'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Aileron (L) Potentiometer', 
                       'Aileron (L) Synchro',
                       'Aileron (L) Inboard',
                       'Aileron (L) Outboard'), available)
    
    def derive(self, pot=P('Aileron (L) Potentiometer'),
               synchro=P('Aileron (L) Synchro'),
               ali=P('Aileron (L) Inboard'),
               alo=P('Aileron (L) Outboard')):
        synchro_samples = 0
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array
        # If Inboard available, use this in preference
        if ali:
            self.array = ali.array
        elif alo:
            self.array = alo.array
        
class AileronRight(DerivedParameterNode):
    # See ElevatorLeft for explanation
    name = 'Aileron (R)'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Aileron (R) Potentiometer', 
                       'Aileron (R) Synchro',
                       'Aileron (R) Inboard',
                       'Aileron (R) Outboard'), available)
    
    def derive(self, pot=P('Aileron (R) Potentiometer'),
               synchro=P('Aileron (R) Synchro'),
               ari=P('Aileron (R) Inboard'),
               aro=P('Aileron (R) Outboard')):

        synchro_samples = 0
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array
        # If Inboard available, use this in preference
        if ari:
            self.array = ari.array
        elif aro:
            self.array = aro.array        

class AileronTrim(DerivedParameterNode): # RollTrim
    '''
    '''
    # TODO: TEST
    align = False
    name = 'Aileron Trim' # Roll Trim
    units = 'deg'

    def derive(self,
               atl=P('Aileron Trim (L)'),
               atr=P('Aileron Trim (R)')):
        self.array, self.frequency, self.offset = blend_two_parameters(atl, atr)


class Elevator(DerivedParameterNode):
    '''
    Blends alternate elevator samples. If either elevator signal is invalid,
    this reverts to just the working sensor.
    '''

    align = False
    units = 'deg'
    
    @classmethod
    def can_operate(cls,available):
        return any_of(('Elevator (L)', 'Elevator (R)'), available)

    def derive(self,
               el=P('Elevator (L)'),
               er=P('Elevator (R)')):

        if el and er:
            self.array, self.frequency, self.offset = blend_two_parameters(el, er)
        else:
            self.array = el.array if el else er.array
            self.frequency = el.frequency if el else er.frequency
            self.offset = el.offset if el else er.offset


class ElevatorLeft(DerivedParameterNode):
    '''
    Specific to a group of ATR aircraft which were progressively modified to
    replace potentiometers with synchros. The data validity tests will mark
    whole parameters invalid, or if both are valid, we want to pick the best
    option.
    '''
    name = 'Elevator (L)'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Elevator (L) Potentiometer', 
                       'Elevator (L) Synchro'), available)
    
    def derive(self, pot=P('Elevator (L) Potentiometer'),
               synchro=P('Elevator (L) Synchro')):

        synchro_samples = 0
        
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
            
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array
        
class ElevatorRight(DerivedParameterNode):
    # See ElevatorLeft for explanation
    name = 'Elevator (R)'
    @classmethod
    def can_operate(cls, available):
        return any_of(('Elevator (R) Potentiometer', 
                       'Elevator (R) Synchro'), available)
    
    def derive(self, pot=P('Elevator (R) Potentiometer'),
               synchro=P('Elevator (R) Synchro')):
        synchro_samples = 0
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array
        
    

    
################################################################################
# Speedbrake


class Speedbrake(DerivedParameterNode):
    '''
    Spoiler angle in degrees, zero flush with the wing and positive up.

    Spoiler positions are recorded in different ways on different aircraft,
    hence the frame specific sections in this class.
    '''

    units = 'deg'
    align = False

    @classmethod
    def can_operate(cls, available):
        '''
        Note: The frame name cannot be accessed within this method to determine
              which parameters are required.
        '''
        return 'Frame' in available and (
            all_of(('Spoiler (2)', 'Spoiler (7)'), available) or
            all_of(('Spoiler (4)', 'Spoiler (9)'), available))
    
    def merge_spoiler(self, spoiler_a, spoiler_b):
        '''
        We indicate the angle of the lower of the two raised spoilers, as
        this represents the drag element. Differential deployment is used to
        augments roll control, so the higher of the two spoilers is relating
        to roll control. Small values are ignored as these arise from control
        trim settings.
        '''
        offset = (spoiler_a.offset + spoiler_b.offset) / 2.0
        array = np.ma.minimum(spoiler_a.array, spoiler_b.array)
        # Force small angles to indicate zero:
        array = np.ma.where(array < 10.0, 0.0, array)
        return array, offset

    def derive(self,
            spoiler_2=P('Spoiler (2)'), spoiler_7=P('Spoiler (7)'),
            spoiler_4=P('Spoiler (4)'), spoiler_9=P('Spoiler (9)'),
            frame=A('Frame')):
        '''
        '''
        frame_name = frame.value if frame else ''

        if frame_name in ['737-3', '737-3A', '737-3B', '737-3C', '737-7']:
            self.array, self.offset = self.merge_spoiler(spoiler_4, spoiler_9)

        elif frame_name in ['737-4', '737-5', '737-5_NON-EIS', '737-6',
                            '737-6_NON-EIS', '737-2227000-335A',
                            'A320_SFIM_ED45_CFM']:
            self.array, self.offset = self.merge_spoiler(spoiler_2, spoiler_7)

        else:
            raise DataFrameError(self.name, frame_name)


class SpeedbrakeSelected(MultistateDerivedParameterNode):
    '''
    Determines the selected state of the speedbrake.

    Speedbrake Selected Values:

    - 0 -- Stowed
    - 1 -- Armed / Commanded (Spoilers Down)
    - 2 -- Deployed / Commanded (Spoilers Up)
    '''

    values_mapping = {
        0: 'Stowed',
        1: 'Armed/Cmd Dn',
        2: 'Deployed/Cmd Up',
    }

    @classmethod
    def can_operate(cls, available):
        '''
        '''
        x = available
        return 'Speedbrake Deployed' in x \
            or ('Family' in x and 'Speedbrake Handle' in x)\
            or ('Family' in x and 'Speedbrake' in x)

    def a320_speedbrake(self, armed, spdbrk):
        '''
        Speedbrake operation for A320 family.
        '''
        array = np.ma.where(spdbrk.array > 1.0,
                            'Deployed/Cmd Up', armed.array)
        return array
        
        
    def b737_speedbrake(self, spdbrk, handle):
        '''
        Speedbrake Handle Positions for 737-x:

            ========    ============
            Angle       Notes
            ========    ============
             0.0        Full Forward
             4.0        Armed
            24.0
            29.0
            38.0        In Flight
            40.0        Straight Up
            48.0        Full Up
            ========    ============

        Speedbrake Positions > 1 = Deployed
        '''
        if spdbrk and handle:
            # Speedbrake and Speedbrake Handle available
            '''
            Speedbrake status taken from surface position. This allows
            for aircraft where the handle is inoperative, overwriting
            whatever the handle position is when the brakes themselves
            have deployed.

            It's not possible to identify when the speedbrakes are just
            armed in this case, so we take any significant motion as
            deployed.

            If there is no handle position recorded, the default 'Stowed'
            value is retained.
            '''
            armed = np.ma.where((2.0 < handle.array) & (handle.array < 35.0),
                                'Armed/Cmd Dn', 'Stowed')
            array = np.ma.where((handle.array >= 35.0) | (spdbrk.array > 1.0),
                                'Deployed/Cmd Up', armed)
        elif spdbrk and not handle:
            # Speedbrake only
            array = np.ma.where(spdbrk.array > 1.0,
                                'Deployed/Cmd Up', 'Stowed')
        elif handle and not spdbrk:
            # Speedbrake Handle only
            armed = np.ma.where((2.0 < handle.array) & (handle.array < 35.0),
                                'Armed/Cmd Dn', 'Stowed')
            array = np.ma.where(handle.array >= 35.0,
                                'Deployed/Cmd Up', armed)
        else:
            raise ValueError("Can't work without either Speedbrake or Handle")
        return array

    def b757_767_speedbrake(self, handle):
        '''
        Speedbrake Handle Positions for 757 & 767:

            ========    ============
              %           Notes
            ========    ============
               0.0        Full Forward
              15.0        Armed
             100.0        Full Up
            ========    ============
        '''
        # Speedbrake Handle only
        armed = np.ma.where((12.0 < handle.array) & (handle.array < 25.0),
                            'Armed/Cmd Dn', 'Stowed')
        array = np.ma.where(handle.array >= 25.0,
                            'Deployed/Cmd Up', armed)
        return array


    def derive(self,
               deployed=M('Speedbrake Deployed'),
               armed=M('Speedbrake Armed'),
               handle=P('Speedbrake Handle'),
               spdbrk=P('Speedbrake'),
               family=A('Family')):

        family_name = family.value if family else ''

        if deployed:
            # We have a speedbrake deployed discrete. Set initial state to
            # stowed, then set armed states if available, and finally set
            # deployed state:
            array = np.ma.zeros(len(deployed.array))
            if armed:
                array[armed.array == 'Armed'] = 1
            array[deployed.array == 'Deployed'] = 2
            self.array = array

        elif 'B737' in family_name:
            self.array = self.b737_speedbrake(spdbrk, handle)

        elif family_name in ['B757', 'B767']:
            self.array = self.b757_767_speedbrake(handle)

        elif family_name == 'A320':
            self.array = self.a320_speedbrake(armed, spdbrk)

        else:
            raise NotImplementedError


class SpeedbrakeHandle(DerivedParameterNode):
    @classmethod
    def can_operate(cls, available):
        return any_of((
            'Speedbrake Handle (L)',
            'Speedbrake Handle (R)',
            'Speedbrake Handle (C)'
        ), available)

    def derive(self,
               sbh_l=M('Speedbrake Handle (L)'),
               sbh_r=M('Speedbrake Handle (R)'),
               sbh_c=M('Speedbrake Handle (C)')):

        available = [par for par in [sbh_l, sbh_r, sbh_c] if par]
        if len(available) > 1:
            self.array = blend_parameters(
                available, self.offset, self.frequency)
        elif len(available) == 1:
            self.array = available[0]


###############################################################################
# Stick Shaker


class StickShaker(MultistateDerivedParameterNode):
    '''
    This accounts for the different types of stick shaker system. Where two
    systems are recorded the results are OR'd to make a single parameter which
    operates in response to either system triggering. Hence the removal of
    automatic alignment of the signals.
    '''

    align = False
    values_mapping = {
        0: '-',
        1: 'Shake',
    }

    @classmethod
    def can_operate(cls, available):
        '''
        '''
        return ('Stick Shaker (L)' in available or \
                'Shaker Activation' in available
                )

    def derive(self, shake_l=M('Stick Shaker (L)'),
            shake_r=M('Stick Shaker (R)'),
            shake_act=M('Shaker Activation')):
        '''
        '''
        if shake_l and shake_r:
            self.array = np.ma.logical_or(shake_l.array, shake_r.array)
            self.frequency , self.offset = shake_l.frequency, shake_l.offset
        
        elif shake_l:
            # Named (L) but in fact (L) and (R) are or'd together at the DAU.
            self.array, self.frequency, self.offset = \
                shake_l.array, shake_l.frequency, shake_l.offset
        
        elif shake_act:
            self.array, self.frequency, self.offset = \
                shake_act.array, shake_act.frequency, shake_act.offset

        else:
            raise NotImplementedError


class ApproachRange(DerivedParameterNode):
    """
    This is the range to the touchdown point for both ILS and visual
    approaches including go-arounds. The reference point is the ILS Localizer
    antenna where the runway is so equipped, or the end of the runway where
    no ILS is available.

    The array is masked where no data has been computed, and provides
    measurements in metres from the reference point where the aircraft is on
    an approach.
    """

    units = 'm'

    @classmethod
    def can_operate(cls, available):
        return all_of((
                    'Airspeed True',
                    'Altitude AAL',
                    'Approach Information'), available) \
                       and any_of(('Heading True', 'Track True', 'Track'
                                   'Heading'), available)

    def derive(self, gspd=P('Groundspeed'),
               glide=P('ILS Glideslope'),
               trk_mag=P('Track'),
               trk_true=P('Track True'),
               hdg_mag=P('Heading'),
               hdg_true=P('Heading True'),
               tas=P('Airspeed True'),
               alt_aal=P('Altitude AAL'),
               approaches=App('Approach Information'),
               ):
        app_range = np_ma_masked_zeros_like(alt_aal.array)

        for approach in approaches:
            # We are going to reference the approach to a runway touchdown
            # point. Without that it's pretty meaningless, so give up now.
            runway = approach.runway
            if not runway:
                continue

            # Retrieve the approach slice
            this_app_slice = approach.slice

            # Let's use the best available information for this approach
            if trk_true and np.ma.count(trk_true.array[this_app_slice]):
                hdg = trk_true
                magnetic = False
            elif trk_mag and np.ma.count(trk_mag.array[this_app_slice]):
                hdg = trk_mag
                magnetic = True
            elif hdg_true and np.ma.count(hdg_true.array[this_app_slice]):
                hdg = hdg_true
                magnetic = False
            else:
                hdg = hdg_mag
                magnetic = True

            kwargs = {'runway': runway}
            
            if magnetic:
                try:
                    # If magnetic heading is being used get magnetic heading
                    # of runway
                    kwargs = {'heading': runway['magnetic_heading']}
                except KeyError:
                    # If magnetic heading is not know for runway fallback to
                    # true heading
                    pass

            # What is the heading with respect to the runway centreline for this approach?
            off_cl = runway_deviation(hdg.array[this_app_slice], **kwargs)

            # Use recorded groundspeed where available, otherwise
            # estimate range using true airspeed. This is because there
            # are aircraft which record ILS but not groundspeed data. In
            # either case the speed is referenced to the runway heading
            # in case of large deviations on the approach or runway.
            if gspd:
                speed = gspd.array[this_app_slice] * \
                    np.cos(np.radians(off_cl))
                freq = gspd.frequency
            
            if not gspd or not np.ma.count(speed):
                speed = tas.array[this_app_slice] * \
                    np.cos(np.radians(off_cl))
                freq = tas.frequency

            # Estimate range by integrating back from zero at the end of the
            # phase to high range values at the start of the phase.
            spd_repaired = repair_mask(speed, repair_duration=None,
                                       extrapolate=True)
            app_range[this_app_slice] = integrate(spd_repaired, freq,
                                                  scale=KTS_TO_MPS,
                                                  direction='reverse')
           
            _, app_slices = slices_between(alt_aal.array[this_app_slice],
                                           100, 500)
            # Computed locally, so app_slices do not need rescaling.
            if len(app_slices) != 1:
                self.info(
                    'Altitude AAL is not between 100-500 ft during an '
                    'approach slice. %s will not be calculated for this '
                    'section.', self.name)
                continue

            # reg_slice is the slice of data over which we will apply a
            # regression process to identify the touchdown point from the
            # height and distance arrays.
            reg_slice = shift_slice(app_slices[0], this_app_slice.start)
            
            gs_est = approach.gs_est
            if gs_est:
                # Compute best fit glidepath. The term (1-0.13 x glideslope
                # deviation) caters for the aircraft deviating from the
                # planned flightpath. 1 dot low is about 7% of a 3 degree
                # glidepath. Not precise, but adequate accuracy for the small
                # error we are correcting for here, and empyrically checked.
                corr, slope, offset = coreg(app_range[reg_slice],
                    alt_aal.array[reg_slice] * (1 - 0.13 * glide.array[reg_slice]))
                # This should correlate very well, and any drop in this is a
                # sign of problems elsewhere.
                if corr < 0.995:
                    self.warning('Low convergence in computing ILS '
                                 'glideslope offset.')
            else:
                # Just work off the height data assuming the pilot was aiming
                # to touchdown close to the glideslope antenna (for a visual
                # approach to an ILS-equipped runway) or at the touchdown
                # zone if no ILS glidepath is installed.
                corr, slope, offset = coreg(app_range[reg_slice],
                                            alt_aal.array[reg_slice])
                # This should still correlate pretty well, though not quite
                # as well as for a directed approach.
                if corr < 0.990:
                    self.warning('Low convergence in computing visual '
                                 'approach path offset.')

            ## This plot code allows the actual flightpath and regression line
            ## to be viewed in case of concern about the performance of the
            ## algorithm.
            ##import matplotlib.pyplot as plt
            ##x1=app_range[gs_est.start:this_app_slice.stop]
            ##y1=alt_aal.array[gs_est.start:this_app_slice.stop]
            ##x2=app_range[gs_est]
            ##y2=alt_aal.array[gs_est] * (1-0.13*glide.array[gs_est])
            ##xnew = np.linspace(np.min(x2),np.max(x2),num=2)
            ##ynew = (xnew - offset)/slope
            ##plt.plot(x1,y1,'-',x2,y2,'-',xnew,ynew,'-')
            ##plt.show()

            # Touchdown point nominally 1000ft from start of runway but
            # use glideslope antenna position if we know it.
            try:
                start_2_loc, gs_2_loc, end_2_loc, pgs_lat, pgs_lon = \
                    runway_distances(runway)
                extend = gs_2_loc
            except (KeyError, TypeError):
                extend = runway_length(runway) - 1000 / METRES_TO_FEET

            # Shift the values in this approach so that the range = 0 at
            # 0ft on the projected ILS or approach slope.
            app_range[this_app_slice] += extend - offset

        self.array = app_range

################################################################################


class VOR1Frequency(DerivedParameterNode):
    """
    Extraction of VOR tuned frequencies from receiver (1).
    """

    name = "VOR (1) Frequency"
    units = 'MHz'

    def derive(self, f=P('ILS-VOR (1) Frequency')):
        self.array = filter_vor_ils_frequencies(f.array, 'VOR')


class VOR2Frequency(DerivedParameterNode):
    """
    Extraction of VOR tuned frequencies from receiver (1).
    """

    name = "VOR (2) Frequency"
    units = 'MHz'

    def derive(self, f=P('ILS-VOR (2) Frequency')):
        self.array = filter_vor_ils_frequencies(f.array, 'VOR')

class WindSpeed(DerivedParameterNode):
    '''
    Required for Embraer 135-145 Data Frame
    '''

    align = False
    units = 'kts'

    def derive(self, wind_1=P('Wind Speed (1)'), wind_2=P('Wind Speed (2)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(wind_1, wind_2)

class WindDirection(DerivedParameterNode):
    '''
    The Embraer 135-145 data frame includes two sources
    '''

    align = False
    units = 'deg'

    def derive(self, wind_1=P('Wind Direction (1)'),
                       wind_2=P('Wind Direction (2)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(wind_1, wind_2)


class WheelSpeedLeft(DerivedParameterNode):
    '''
    Merge the various recorded wheel speed signals from the left hand bogie.
    '''
    name = 'Wheel Speed (L)'
    align = False

    @classmethod
    def can_operate(cls, available):
        return 'Wheel Speed (L) (1)' in available
    
    def derive(self, ws_1=P('Wheel Speed (L) (1)'), ws_2=P('Wheel Speed (L) (2)'),
               ws_3=P('Wheel Speed (L) (3)'), ws_4=P('Wheel Speed (L) (4)')):
        sources = [ws_1, ws_2, ws_3, ws_4]
        self.offset = 0.0
        self.frequency = 4.0
        self.array = blend_parameters(sources, self.offset, self.frequency)



class WheelSpeedRight(DerivedParameterNode):
    '''
    Merge the various recorded wheel speed signals from the right hand bogie.
    '''
    name = 'Wheel Speed (R)'
    align = False
    
    @classmethod
    def can_operate(cls, available):
        return 'Wheel Speed (R) (1)' in available

    def derive(self, ws_1=P('Wheel Speed (R) (1)'), ws_2=P('Wheel Speed (R) (2)'),
               ws_3=P('Wheel Speed (R) (3)'), ws_4=P('Wheel Speed (R) (4)')):
        sources = [ws_1, ws_2, ws_3, ws_4]
        self.offset = 0.0
        self.frequency = 4.0
        self.array = blend_parameters(sources, self.offset, self.frequency)


class WheelSpeed(DerivedParameterNode):
    '''
    Merge Left and Right wheel speeds.
    
    Q: Should wheel speed Centre (C) be merged too?
    '''
    align = False
    
    def derive(self, ws_l=P('Wheel Speed (L)'), ws_r=P('Wheel Speed (R)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(ws_l, ws_r)


class TrackContinuous(DerivedParameterNode):
    '''
    Magnetic Track Heading Continuous of the Aircraft by adding Drift from track
    to the aircraft Heading.
    '''
    units = 'deg'
    
    def derive(self, heading=P('Heading Continuous'), drift=P('Drift')):
        #Note: drift is to the right of heading, so: Track = Heading + Drift
        self.array = heading.array + drift.array


class Track(DerivedParameterNode):
    '''
    Magnetic Track Heading of the Aircraft by adding Drift from track to the
    aircraft Heading.

    Range 0 to 360
    '''
    units = 'deg'

    def derive(self, track=P('Track Continuous')):
        self.array = track.array % 360


class TrackTrueContinuous(DerivedParameterNode):
    '''
    True Track Heading Continuous of the Aircraft by adding Drift from track to
    the aircraft Heading.
    '''
    units = 'deg'
    
    def derive(self, heading=P('Heading True Continuous'), drift=P('Drift')):
        #Note: drift is to the right of heading, so: Track = Heading + Drift
        self.array = heading.array + drift.array


class TrackTrue(DerivedParameterNode):
    '''
    True Track Heading of the Aircraft by adding Drift from track to the
    aircraft Heading.

    Range 0 to 360
    '''
    units = 'deg'

    def derive(self, track_true=P('Track True Continuous')):
        self.array = track_true.array % 360


class TrackDeviationFromRunway(DerivedParameterNode):
    '''
    Difference from the aircraft's Track angle and that of the Runway
    centreline. Measured during Takeoff and Approach phases.

    Based on Track True angle in order to avoid complications with magnetic
    deviation values recorded at airports. The deviation from runway centre
    line would be the same whether the calculation is based on Magnetic or
    True measurements.
    '''
    # forse offset for approach slice start consistency
    align_frequency = 1
    align_offset = 0

    @classmethod
    def can_operate(cls, available):
        return any_of(('Approach Information', 'FDR Takeoff Runway'), available) \
               and any_of(('Track Continuous', 'Track True Continuous'), available)

    def _track_deviation(self, array, _slice, rwy, magnetic=False):
        if magnetic:
            try:
                # If magnetic heading is being used get magnetic heading
                # of runway
                self.array[_slice] = runway_deviation(
                     array[_slice], heading=rwy['magnetic_heading'])
                return
            except KeyError:
                # If magnetic heading is not know for runway fallback to
                # true heading
                pass
        try:
            self.array[_slice] = runway_deviation(array[_slice], runway=rwy)
        except ValueError:
            # could not determine runway information
            return

    def derive(self, track_true=P('Track True Continuous'),
               track_mag=P('Track Continuous'),
               takeoff=S('Takeoff'),
               to_rwy=A('FDR Takeoff Runway'),
               apps=App('Approach Information')):
        
        if track_true:
            magnetic = False
            track = track_true
        else:
            magnetic = True
            track = track_mag

        self.array = np_ma_masked_zeros_like(track.array)

        for app in apps:
            if not app.runway:
                self.warning("Cannot calculate TrackDeviationFromRunway for "
                             "approach as there is no runway.")
                continue
            self._track_deviation(track.array, app.slice, app.runway, magnetic)

        if to_rwy:
            self._track_deviation(track.array, takeoff[0].slice, to_rwy.value,
                                  magnetic)


class StableApproach(MultistateDerivedParameterNode):
    '''
    During the Approach, the following steps are assessed for stability:

    1. Gear is down
    2. Landing Flap is set
    3. Heading aligned to Runway within 10 degrees
    4. Approach Airspeed minus Reference speed within 20 knots
    5. Glideslope deviation within 1 dot
    6. Localizer deviation within 1 dot
    7. Vertical speed between -1000 and -200 fpm
    8. Engine Power greater than 45% # TODO: and not Cycling within last 5 seconds

    if all the above steps are met, the result is the declaration of:
    9. "Stable"
    
    If Vapp is recorded, a more constraint airspeed threshold is applied.
    Where parameters are not monitored below a certain threshold (e.g. ILS
    below 200ft) the stability criteria just before 200ft is reached is
    continued through to landing. So if one was unstable due to ILS
    Glideslope down to 200ft, that stability is assumed to continue through
    to landing.

    TODO/REVIEW:
    ============
    * Check for 300ft limit if turning onto runway late and ignore stability criteria before this? Alternatively only assess criteria when heading is within 50.
    * Q: Fill masked values of parameters with False (unstable: stop here) or True (stable, carry on)
    * Add hysteresis (3 second gliding windows for GS / LOC etc.)
    * Engine cycling check
    * Check Boeing's Vref as one may add an increment to this (20kts) which is not recorded!
    '''

    values_mapping = {
        0: '-',  # All values should be masked anyway, this helps align values
        1: 'Gear Not Down',
        2: 'Not Landing Flap',
        3: 'Hdg Not Aligned',   # Rename with heading?
        4: 'Aspd Not Stable',  # Q: Split into two Airspeed High/Low?
        5: 'GS Not Stable',
        6: 'Loc Not Stable',
        7: 'IVV Not Stable',
        8: 'Eng N1 Not Stable',
        9: 'Stable',
    }

    align_frequency = 1  # force to 1Hz

    @classmethod
    def can_operate(cls, available):
        # Commented out optional dependencies
        # Airspeed Relative, ILS and Vapp are optional
        deps = ['Approach And Landing', 'Gear Down', 'Flap', 
                'Track Deviation From Runway',
                #'Airspeed Relative For 3 Sec', 
                'Vertical Speed', 
                #'ILS Glideslope', 'ILS Localizer',
                #'Eng (*) N1 Min For 5 Sec', 
                'Altitude AAL',
                #'Vapp',
                ]
        return all_of(deps, available) and (
            'Eng (*) N1 Min For 5 Sec' in available or \
            'Eng (*) EPR Min For 5 Sec' in available)
        
    
    def derive(self,
               apps=S('Approach And Landing'),
               gear=M('Gear Down'),
               flap=M('Flap'),
               tdev=P('Track Deviation From Runway'),
               aspd=P('Airspeed Relative For 3 Sec'),
               vspd=P('Vertical Speed'),
               gdev=P('ILS Glideslope'),
               ldev=P('ILS Localizer'),
               eng_n1=P('Eng (*) N1 Min For 5 Sec'),
               eng_epr=P('Eng (*) EPR Min For 5 Sec'),
               alt=P('Altitude AAL'),
               vapp=P('Vapp'),
               ):
      
        #Ht AAL due to
        # the altitude above airfield level corresponding to each cause
        # options are FLAP, GEAR GS HI/LO, LOC, SPD HI/LO and VSI HI/LO

        # create an empty fully masked array
        self.array = np.ma.zeros(len(alt.array))
        self.array.mask = True
        # shortcut for repairing masks
        repair = lambda ar, ap: repair_mask(ar[ap], zero_if_masked=True)

        for approach in apps:
            # Restrict slice to 10 seconds after landing if we hit the ground
            gnd = index_at_value(alt.array, 0, approach.slice)
            if gnd and gnd + 10 < approach.slice.stop:
                stop = gnd + 10
            else:
                stop = approach.slice.stop
            _slice = slice(approach.slice.start, stop)
            # prepare data for this appproach:
            gear_down = repair(gear.array, _slice)
            flap_lever = repair(flap.array, _slice)
            track_dev = repair(tdev.array, _slice)
            airspeed = repair(aspd.array, _slice) if aspd else None  # optional
            glideslope = repair(gdev.array, _slice) if gdev else None  # optional
            localizer = repair(ldev.array, _slice) if ldev else None  # optional
            # apply quite a large moving average to smooth over peaks and troughs
            vertical_speed = moving_average(repair(vspd.array, _slice), 10)
            if eng_epr:
                # use EPR if available
                engine = repair(eng_epr.array, _slice)
            else:
                engine = repair(eng_n1.array, _slice)
            altitude = repair(alt.array, _slice)
            
            index_at_50 = index_closest_value(altitude, 50)
            index_at_200 = index_closest_value(altitude, 200)

            # Determine whether Glideslope was used at 1000ft, if not ignore ILS
            glide_est_at_1000ft = False
            if gdev and ldev:
                _1000 = index_at_value(altitude, 1000)
                if _1000:
                    # If masked at 1000ft; bool(np.ma.masked) == False
                    glide_est_at_1000ft = abs(glideslope[_1000]) < 1.5  # dots

            #== 1. Gear Down ==
            # Assume unstable due to Gear Down at first
            self.array[_slice] = 1
            landing_gear_set = (gear_down == 'Down')
            stable = landing_gear_set.filled(True)  # assume stable (gear down)

            #== 2. Landing Flap ==
            # not due to landing gear so try to prove it wasn't due to Landing Flap
            self.array[_slice][stable] = 2
            landing_flap = last_valid_sample(flap_lever)
            landing_flap_set = (flap_lever == landing_flap.value)
            stable &= landing_flap_set.filled(True)  # assume stable (flap set)

            #== 3. Heading ==
            self.array[_slice][stable] = 3
            STABLE_HEADING = 10  # degrees
            stable_track_dev = abs(track_dev) <= STABLE_HEADING
            stable &= stable_track_dev.filled(True)  # assume stable (on track)

            if aspd:
                #== 4. Airspeed Relative ==
                self.array[_slice][stable] = 4
                if vapp:
                    # Those aircraft which record a variable Vapp shall have more constraint thresholds
                    STABLE_AIRSPEED_BELOW_REF = -5
                    STABLE_AIRSPEED_ABOVE_REF = 10
                else:
                    # Most aircraft records only Vref - as we don't know the wind correction more lenient
                    STABLE_AIRSPEED_BELOW_REF = 0
                    STABLE_AIRSPEED_ABOVE_REF = 30
                stable_airspeed = (airspeed >= STABLE_AIRSPEED_BELOW_REF) & (airspeed <= STABLE_AIRSPEED_ABOVE_REF)
                # extend the stability at the end of the altitude threshold through to landing
                stable_airspeed[altitude < 50] = stable_airspeed[index_at_50]
                stable &= stable_airspeed.filled(True)  # if no V Ref speed, values are masked so consider stable as one is not flying to the vref speed??

            if glide_est_at_1000ft:
                #== 5. Glideslope Deviation ==
                self.array[_slice][stable] = 5
                STABLE_GLIDESLOPE = 1.0  # dots
                stable_gs = (abs(glideslope) <= STABLE_GLIDESLOPE)
                # extend the stability at the end of the altitude threshold through to landing
                stable_gs[altitude < 200] = stable_gs[index_at_200]
                stable &= stable_gs.filled(False)  # masked values are usually because they are way outside of range and short spikes will have been repaired

                #== 6. Localizer Deviation ==
                self.array[_slice][stable] = 6
                STABLE_LOCALIZER = 1.0  # dots
                stable_loc = (abs(localizer) <= STABLE_LOCALIZER)
                # extend the stability at the end of the altitude threshold through to landing
                stable_loc[altitude < 200] = stable_loc[index_at_200]
                stable &= stable_loc.filled(False)  # masked values are usually because they are way outside of range and short spikes will have been repaired

            #== 7. Vertical Speed ==
            self.array[_slice][stable] = 7
            STABLE_VERTICAL_SPEED_MIN = -1000
            STABLE_VERTICAL_SPEED_MAX = -200
            stable_vert = (vertical_speed >= STABLE_VERTICAL_SPEED_MIN) & (vertical_speed <= STABLE_VERTICAL_SPEED_MAX) 
            # extend the stability at the end of the altitude threshold through to landing
            stable_vert[altitude < 50] = stable_vert[index_at_50]
            stable &= stable_vert.filled(True)
            
            #== 8. Engine Power (N1) ==
            self.array[_slice][stable] = 8
            # TODO: Patch this value depending upon aircraft type
            STABLE_N1_MIN = 45  # %
            STABLE_EPR_MIN = 1.1
            eng_minimum = STABLE_EPR_MIN if eng_epr else STABLE_N1_MIN
            stable_engine = (engine >= eng_minimum)
            stable_engine |= (altitude > 1000)  # Only use in altitude band below 1000 feet
            # extend the stability at the end of the altitude threshold through to landing
            stable_engine[altitude < 50] = stable_engine[index_at_50]
            stable &= stable_engine.filled(True)

            #== 9. Stable ==
            # Congratulations; whatever remains in this approach is stable!
            self.array[_slice][stable] = 9

        #endfor
        return



class ElevatorActuatorMismatch(DerivedParameterNode):
    '''
    An incident focused attention on mismatch between the elevator actuator
    and surface. This parameter is designed to measure the mismatch which
    should never be large, and from which we may be able to predict actuator
    malfunctions.
    '''
    def derive(self, elevator=P('Elevator'), 
               ap=M('AP Engaged'), 
               fcc=M('FCC Local Limited Master'),
               left=P('Elevator (L) Actuator'), 
               right=P('Elevator (R) Actuator')):
        
        scaling = 1/2.6 # 737 elevator specific at this time
        
        fcc_l = np.ma.where(fcc.array == 'FCC (L)', 1, 0)
        fcc_r = np.ma.where(fcc.array == 'FCC (R)', 1, 0)
        
        amm = actuator_mismatch(ap.array.raw, 
                                fcc_l,
                                fcc_r,
                                left.array,
                                right.array,
                                elevator.array,
                                scaling,
                                self.frequency)
        
        self.array = amm


class MasterWarning(MultistateDerivedParameterNode):
    '''
    Combine master warning for captain and first officer.
    '''

    values_mapping = {0: '-', 1: 'Warning'}

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               warn_capt=M('Master Warning (Capt)'),
               warn_fo=M('Master Warning (FO)')):

        self.array = vstack_params_where_state(
            (warn_capt, 'Warning'),
            (warn_fo, 'Warning'),
        ).any(axis=0)


class PitchAlternateLaw(MultistateDerivedParameterNode):
    '''
    Combine Pitch Alternate Law from sources (1) and/or (2).
    
    TODO: Review
    '''

    values_mapping = {0: '-', 1: 'Alternate'}

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               alt_law_1=M('Pitch Alternate (1) Law'),
               alt_law_2=M('Pitch Alternate (2) Law')):

        self.array = vstack_params_where_state(
            (alt_law_1, 'Alternate'),
            (alt_law_2, 'Alternate'),
        ).any(axis=0)
