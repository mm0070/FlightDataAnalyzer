import inspect
import logging
import numpy as np
import re
import copy
import math

from abc import ABCMeta
from collections import namedtuple, Iterable
from itertools import product
from operator import attrgetter

from analysis_engine.library import (align, find_edges, is_index_within_slice,
                                     is_slice_within_slice, slices_above,
                                     slices_below, slices_between,
                                     slices_from_to, slices_overlap,
                                     value_at_index, value_at_time)
from analysis_engine.recordtype import recordtype

# FIXME: a better place for this class
from hdfaccess.parameter import MappedArray


logger = logging.getLogger(name=__name__)

# Define named tuples for KPV and KTI and FlightPhase
KeyPointValue = recordtype('KeyPointValue',
                           'index value name slice datetime latitude longitude', 
                           field_defaults={'slice':slice(None)}, default=None)
KeyTimeInstance = recordtype('KeyTimeInstance',
                             'index name datetime latitude longitude', 
                             default=None)
Section = namedtuple('Section', 'name slice start_edge stop_edge') #Q: rename mask -> slice/section

# Ref: django/db/models/options.py:20
# Calculate the verbose_name by converting from InitialCaps to "lowercase with spaces".
def get_verbose_name(class_name):
    '''
    :type class_name: str
    :rtype: str
    '''
    if re.match('^_\d.*$', class_name):
        # Remove initial underscore to allow class names starting with numbers
        # e.g. '_1000FtInClimb' will become '1000 Ft In Climb'
        class_name = class_name[1:]
    return re.sub('(((?<=[a-z])[A-Z0-9])|([A-Z0-9](?![A-Z0-9]|$)))', ' \\1',
                  class_name).lower().strip()


def powerset(iterable):
    """
    Ref: http://docs.python.org/library/itertools.html#recipes
    powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)
    
    :rtype: itertools.chain
    """
    from itertools import chain, combinations
    s = list(iterable)
    return chain.from_iterable(combinations(s, r) for r in range(len(s)+1))


def get_param_kwarg_names(method):
    """
    Inspects a method's arguments and returns the defaults values of keyword
    arguments defined in the method.
    
    Raises ValueError if there are any args defined other than "self".
    
    :param method: Method to be inspected
    :type method: method
    :returns: Ordered list of default values of keyword arguments
    :rtype: list
    """
    args, varargs, varkw, defaults = inspect.getargspec(method)
    if not defaults or args[:-len(defaults)] != ['self'] or varargs:
        raise ValueError("Node '%s' must have kwargs, cannot accept no kwargs "
                         "or any args other than 'self'. args:'%s' *args:'%s'"
                         % (method.im_class.get_name(), args[1:], varargs))
    if varkw:
        # One day, could insert all available params as kwargs - but cannot
        # guarentee requirements will work
        raise NotImplementedError("Cannot define **kwargs")
    # alternative: return dict(zip(defaults, args[-len(defaults):]))
    return defaults

#------------------------------------------------------------------------------
# Abstract Node Classes
# =====================
class Node(object):
    __metaclass__ = ABCMeta

    name = '' # Optional, default taken from ClassName
    align_to_first_dependency = True
    data_type = None # Q: What should the default be? Q: Should this dictate the numpy dtype saved to the HDF file or should it be inferred from the array?
        
    def __init__(self, name='', frequency=1, offset=0, **kwargs):
        """
        Abstract Node. frequency and offset arguments are populated from the
        first available dependency parameter object.
        
        Kwargs get lost - allows "hz" to be ignored when passed with "frequency" 
        
        :param name: Name of parameter
        :type params: str
        :param frequency: Sample Rate / Frequency / Hz
        :type frequency: Int
        :param offset: Offset in Frame.
        :type offset: Float
        """
        assert kwargs.get('hz', frequency) == frequency, "Differing freq to hz"
        if name:
            self.name = name + '' # for ease of testing, checks name is string ;-)
        else:
            self.name = self.get_name() # usual option
        self.frequency = self.sample_rate = self.hz = frequency # Hz
        self.offset = offset # secs
        
    def __repr__(self):
        '''
        :rtype: str
        '''
        #TODO: Add __class__.__name__?
        return "%s %sHz %.2fsecs" % (self.get_name(), self.frequency, self.offset)
    
    @property
    def node_type(self):
        '''
        :returns: Node base class.
        :rtype: class
        '''
        # XXX: May break if we adopt multi-inheritance or a different class 
        # hierarchy.
        return self.__class__.__base__
    
    @classmethod
    def get_name(cls):
        """ class My2BNode -> 'My2B Node'
        
        :rtype: str
        """
        return cls.name or get_verbose_name(cls.__name__).title()
    
    @classmethod
    def get_dependency_names(cls):
        """
        :returns: A list of dependency names.
        :rtype: [str]
        """
        # TypeError:'ABCMeta' object is not iterable?
        # this probably means dependencies for this class isn't a list!
        params = get_param_kwarg_names(cls.derive)
        return [d.name or d.get_name() for d in params]
    
    @classmethod
    def can_operate(cls, available):
        """
        Compares the string names of all dependencies against those available.
        
        This is a classmethod, so please remember to use the
        @classmethod decorator when overriding! (if you forget - it will break)
        
        :param available: Available parameters from the dependency tree
        :type available: list of strings
        :returns: True if dependencies is a subset of available. For more specific operational requirements, override appropriately.
        :rtype: bool
        
        Sample overrides for "Any deps available":
@classmethod
def can_operate(cls, available):
    # works with any combination of params available
    return any(d in available for d in cls.get_dependency_names())

@classmethod
def can_operate(cls, available):
    # Can operate if any are available.
    return set(cls.get_dependency_names()).intersection(available)
            
        """
        # ensure all names are strings
        return all(x in available for x in cls.get_dependency_names())
        
    @classmethod
    def get_operational_combinations(cls):
        """
        :returns: Every operational combination of dependencies.
        :rtype: [str]
        """
        dependencies_powerset = powerset(cls.get_dependency_names())
        return [args for args in dependencies_powerset if cls.can_operate(args)]
    
    def get_aligned(self, align_to_param):
        """
        :returns: version of self which is aligned to the incoming argument.
        :rtype: self.__class__
        """
        raise NotImplementedError("Abstract Method")
    
    def get_derived(self, args):
        """
        Accessor for derive method which first aligns all parameters to the
        first to ensure parameter data and indices are consistent.
        
        :param args: List of available Parameter objects
        :type args: list
        :returns: self after having aligned dependencies and called derive.
        :rtype: self
        """
        if self.align_to_first_dependency:
            try:
                i, first_param = next(((n, a) for n, a in enumerate(args) if \
                                       a is not None and a.frequency))
            except StopIteration:
                pass
            else:
                for n, param in enumerate(args):
                    # if param is set and it's after the first dependency
                    if param and n > i:
                         # override argument in list in-place
                        args[n] = param.get_aligned(first_param)
        res = self.derive(*args)
        if res is NotImplemented:
            raise NotImplementedError("Class '%s' derive method is not implemented." % \
                                      self.__class__.__name__)
        elif res:
            raise UserWarning("Class '%s' should not have returned anything. Got: %s" % (
                self.__class__.__name__, res))
        return self
        
    def derive(self, **kwargs):
        """
        Accepts keyword arguments where the default determines the derive
        dependencies. Each keyword default must be a Parameter like object
        with attribute name or method get_name() returning a string
        representation of the Parameter.
        
        e.g. def derive(self, first_dep=P('not_available'), first_available=P('available'), another=MyDerivedNode:
                 pass
        
        Note: Although keywords are required to determine the derive method's 
        dependencies, Implementation actually provides the keywords using 
        positional arguments, providing None where the dependency is not 
        available.
        
        e.g. deps = [None, param_obj]
             node.derive(*deps)
             
        Results of derive are saved onto the object's attributes. See each
        implementation of Node.
        
        e.g. self.array = []
        
        Note: All params masked arrays can be manipulated as required within
        the scope of this method without affecting any other Node classes.
        This is because we write all results back to the hdf, therefore you
        cannot damage the interim numpy masked arrays.
        
        If an implementation does not adhere to the mask of an array, ensure
        that you document it in the docstring as follows:
        WARNING: Does not adhere to the MASK.
        
        :param kwargs: Keyword arguments where default is a Parameter object or Node class
        :type kwargs: dict
        :returns: No returns! Sets attributes on self to be accessed after calling derive.
        :rtype: None
        """
        raise NotImplementedError("Abstract Method")
    
    # Logging
    ############################################################################

    def _get_logger(self):
        """
        :returns: A logger with name based on module and class name.
        """
        # # FIXME: storing logger as Node attribute is causing problems as we
        # # deepcopy() the Node objects the loggers are copied as well. This
        # # has side-effects.
        # # logging.getLogger(logger_name) is using global dictionary, so it
        # # does not seem to be an expensive operation.
        # if not self._logger:
        #     # Set up self._logger
        #     self._logger = logging.getLogger('%s.%s' % (
        #         self.__class__.__module__,
        #         self.__class__.__name__,
        #     ))
        # return self._logger
        return logging.getLogger('%s.%s' % (
            self.__class__.__module__,
            self.__class__.__name__,
        ))

    def debug(self, *args, **kwargs):
        """
        Log a debug level message.
        
        :rtype: None
        """
        logger = self._get_logger()
        logger.debug(*args, **kwargs)    
    
    def error(self, *args, **kwargs):
        """
        Log an error level message.
        
        :rtype: None
        """
        logger = self._get_logger()
        logger.error(*args, **kwargs)
    
    def exception(self, *args, **kwargs):
        """
        Log an exception level message.
        
        :rtype: None
        """
        logger = self._get_logger()
        logger.exception(*args, **kwargs)            
    
    def info(self, *args, **kwargs):
        """
        Log an info level message.
        
        :rtype: None
        """
        logger = self._get_logger()
        logger.info(*args, **kwargs)
    
    def warning(self, *args, **kwargs):
        """
        Log a warning level message.
        
        :rtype: None
        """
        logger = self._get_logger()
        logger.warning(*args, **kwargs)


class DerivedParameterNode(Node):
    """
    Base class for DerivedParameters which overide def derive() method.
    
    Also used during processing when creating parameters from HDF files as
    dependencies for other Nodes.
    """
    # The units which the derived parameter's array is measured in. It is in
    # lower case to be consistent with the HDFAccess Parameter class and
    # therefore written as an attribute to the HDF file.
    units = None
    data_type = 'Derived'
    lfl = False
    
    def __init__(self, name='', array=np.ma.array([]), frequency=1, offset=0,
                 data_type=None, *args, **kwargs):
        # create array results placeholder
        self.array = array # np.ma.array derive result goes here!
        if not self.data_type:
            self.data_type = data_type
        super(DerivedParameterNode, self).__init__(name=name,
                                                   frequency=frequency, 
                                                   offset=offset, 
                                                   *args, **kwargs)
        
    def at(self, secs):
        """
        Gets the value within the array at time secs. Interpolates to retrieve
        the most accurate value.
        
        :param secs: time delta from start of data in seconds
        :type secs: float or timedelta
        :returns: The interpolated value of the array at time secs.
        :rtype: float
        """
        if secs is None:
            return None
        try:
            # get seconds from timedelta
            secs = float(secs.total_seconds)
        except AttributeError:
            # secs is a float
            secs = float(secs)
        return value_at_time(self.array, self.frequency, self.offset, secs)
        
    def get_aligned(self, param):
        '''
        :param param: Node to align copy to.
        :type param: Node subclass
        :returns: A copy of self aligned to the input parameter.
        :rtype: DerivedParameterNode
        '''
        # Create temporary new aligned parameter of correct type:
        aligned_param = self.__class__(
            name=self.name,
            frequency=param.frequency,
            offset=param.offset,
        )

        # Align the array for the temporary parameter:
        aligned_param.array = align(self, param)
        
        # Ensure that we copy attributes required for multi-states:
        if hasattr(self, 'values_mapping'):
            aligned_param.values_mapping = self.values_mapping

        return aligned_param 
    
    def slices_above(self, value):
        '''
        Get slices where the parameter's array is above value.
        
        :param value: Value to create slices above.
        :type value: float or int
        :returns: Slices where the array is above a certain value.
        :rtype: list of slice
        '''
        return slices_above(self.array, value)[1]
    
    def slices_below(self, value):
        '''
        Get slices where the parameter's array is below value. Note: It is
        normally recommended to use slices_between and specify the lower
        bound in preference to slices_below, as this is normally more robust.
        
        :param value: Value to create slices below.
        :type value: float or int
        :returns: Slices where the array is below a certain value.
        :rtype: list of slice
        '''
        return slices_below(self.array, value)[1]
    
    def slices_between(self, min_, max_):
        '''
        Get slices where the parameter's array values are between min_ and
        max_.
        
        :param min_: Minimum value within slice.
        :type min_: float or int
        :param max_: Maximum value within slice.
        :type max_: float or int
        :returns: Slices where the array is within min_ and max_.
        :rtype: list of slice
        '''
        return slices_between(self.array, min_, max_)[1]
    
    def slices_from_to(self, from_, to):
        '''
        Get slices of the parameter's array where values are between from_
        and to, and either ascending or descending depending on whether from_ 
        is greater than or less than to. For instance,
        param.slices_from_to(1000, 1500) is ascending and requires will only 
        return slices where values are between 1000 and 1500 if
        the value in the array at the start of the slice is less than the value
        at the stop. The opposite condition would be applied if the arguments
        are descending, e.g. slices_from_to(array, 1500, 1000).
        
        :param array:
        :type array: np.ma.masked_array
        :param from_: Value from.
        :type from_: float or int
        :param to: Value to.
        :type to: float or int
        :returns: Slices of the array where values are between from_ and to and either ascending or descending depending on comparing from_ and to.
        :rtype: list of slice
        '''
        return slices_from_to(self.array, from_, to)[1]

    def slices_to_kti(self, ht, tdwns):
        '''
        Provides a slice across a height range ending precisely at the point of
        touchdown, rather than the less precise altitude aal moment of touchdown.
        
        :param self: Reference to the height (normally Altitude AAL) Parameter
        :param ht: Starting height for the slices
        :param tdwns: Reference to the Touchdown KTIs
        '''
        result = [] # We are going to return a list of slices.
        _, basics = slices_from_to(self.array, ht, 0)
        for basic in basics:
            new_basic = slice(basic.start, min(basic.stop + 20, len(self.array))) # In case the touchdown is behind the basic slice.
            for tdwn in tdwns:
                if is_index_within_slice(tdwn.index, new_basic):
                    result.append(slice(new_basic.start, tdwn.index))
                    break
        return result


P = Parameter = DerivedParameterNode # shorthand

def multistate_string_to_integer(string_array, mapping):
    """
    Converts (['one', 'two'], {1:'one', 2:'two'}) to [1, 2]
    
    Works on the masked array's data, therefore maintains the mask and
    converts all masked and non-masked values.
    
    Note: If string_array is of mixed dtype (dtype == object),
    floats/integers will be converted in int_array even if not in the
    mapping.
    
    :param string_array: Array to be converted
    :type string_array: np.ma.array(dtype=string,object,...)
    :param mapping: mapping of values to convert {from_this : to_this}
    :type mapping: dict
    :returns: Integer array
    :rtype: np.ma.array(dtype=int)
    """
    if not len(string_array):
        return string_array

    output_array = string_array.copy()
    # values need converting using mapping
    for int_value, str_value in mapping.iteritems():
        output_array.data[string_array.data == str_value] = int_value
    output_array.fill_value = 999999  #NB: only 999 will be stored by dtype
    # apply fill_value to all masked values
    output_array.data[np.ma.where(output_array.mask)] = output_array.fill_value
    try:
        int_array = output_array.astype(int)
    except ValueError as err:
        msg = "No value in values_mapping found for %s" % str(err).split("'")[-2]
        raise ValueError(msg)
    return int_array


class MultistateDerivedParameterNode(DerivedParameterNode):
    '''
    MappedArray stored as array will be of integer dtype
    '''
    data_type = 'Derived Multi-state'
    
    def __init__(self, name='', array=np.ma.array([]), frequency=1, offset=0,
                 data_type=None, values_mapping={}, *args, **kwargs):
        
        #Q: if no values_mapping set to None?
        if values_mapping:
            self.values_mapping = values_mapping
        elif not hasattr(self, 'values_mapping'):
            self.values_mapping = {}

        super(MultistateDerivedParameterNode, self).__init__(
                name, array, frequency, offset, data_type, *args,
                **kwargs)

    def __setattr__(self, name, value):
        '''
        Prepare self.array

        `value` can be:
            * a MappedArray: the value is assigned with no change,
            * a MaskedArray: value is converted to MaskedArray with no change
              to the raw data
            * a list: value is interpreted as 'converted' data, so the mapping
              is reversed. KeyError is raised if the values are not found in
              the mapping.
        
        :type name: str
        :type value: MappedArray or MaskedArray or []
        :raises ValueError: if incomplete mapping for array string values.
        '''
        if name not in ('array', 'values_mapping'):
            return super(MultistateDerivedParameterNode, self). \
                    __setattr__(name, value)

        if name == 'values_mapping':
            if hasattr(self, 'array'):
                self.array.values_mapping = value
            return object.__setattr__(self, name, value)
        if isinstance(value, MappedArray):
            # enforce own values mapping on the data
            value.values_mapping = self.values_mapping
        elif isinstance(value, np.ma.MaskedArray):
            if value.dtype == int:
                # NB: Removed allowance for float!
                int_array = value
            else:
                # can be of type string or object (mixed)
                int_array = multistate_string_to_integer(value, self.values_mapping)
            value = MappedArray(int_array, values_mapping=self.values_mapping)
        elif isinstance(value, Iterable):
            # assume a list of mapped values
            reversed_mapping = {v: k for k, v in self.values_mapping.items()}
            data = [int(reversed_mapping[v]) for v in value]
            value = MappedArray(data, values_mapping=self.values_mapping)
        else:
            raise ValueError('Invalid argument type assigned to array: %s'
                             % type(value))

        return object.__setattr__(self, name, value)


M = MultistateDerivedParameterNode  # shorthand


def derived_param_from_hdf(hdf, name):
    '''
    Loads and wraps an HDF parameter with either DerivedParameterNode or
    MultistateDerivedParameterNode classes.
    
    :type hdf: hdf_file
    :param name: Parameter name to load from the HDF file.
    :type name: str
    :rtype: DerivedParameterNode or MultistateDerivedParameterNode
    '''
    hdf_parameter = hdf[name]
    if isinstance(hdf_parameter.array, MappedArray):
        result = MultistateDerivedParameterNode(
            name=hdf_parameter.name, array=hdf_parameter.array,
            frequency=hdf_parameter.frequency, offset=hdf_parameter.offset,
            data_type=hdf_parameter.data_type,
            values_mapping=hdf_parameter.values_mapping
        )
        return result

    else:
        return Parameter(
            name=hdf_parameter.name, array=hdf_parameter.array,
            frequency=hdf_parameter.frequency, offset=hdf_parameter.offset,
            data_type=hdf_parameter.data_type
        )


class SectionNode(Node, list):
    '''
    Derives from list to implement iteration and list methods.
    
    Is a list of Section namedtuples, each with attributes .name, .slice,
    .start_edge and .stop_edge
    '''
    def __init__(self, *args, **kwargs):
        '''
        List of slices where this phase is active. Has a frequency and offset.
        
        :param items: Optional keyword argument of initial items to be contained within self.
        :type items: list
        '''
        if 'items' in kwargs:
            self.extend(kwargs['items'])
            del kwargs['items']
        super(SectionNode, self).__init__(*args, **kwargs)

    def create_section(self, section_slice, name='', begin=None, end=None):
        """
        Create a slice of the data.
        
        NOTE: Sections with slice start/ends of None can cause errors later
        when creating KPV/KTIs from a slice. However, they are valid for
        slicing data arrays from.
        
        :type section_slice: slice
        :type name: str
        :type begin: int or float
        :type end: int or float
        :rtype: None
        """
        if section_slice.start is None or section_slice.stop is None:
            logger.debug("Section %s created %s with None start or stop.", 
                          self.get_name(), section_slice)
        section = Section(name or self.get_name(), section_slice, 
                          begin or section_slice.start, 
                          end or section_slice.stop)
        self.append(section)
        
    def create_sections(self, section_slices, name=''):
        '''
        :type section_slices: [slice]
        :type name: str
        '''
        for sect in section_slices:
            self.create_section(sect, name=name)
        
    #TODO: Accessor for 1Hz slice, 8Hz slice etc.
    def get_aligned(self, param):
        '''
        Creates a copy with section slices aligned to the frequency and offset
        of param.
        
        :param param: Parameter to align the copy of self to.
        :type param: Parameter object
        :returns: An object of the same type as self containing matching elements.
        :rtype: self.__class__
        '''
        aligned_node = self.__class__(frequency=param.frequency,
                                      offset=param.offset)
        
        multiplier = param.frequency / self.frequency
        offset = (self.offset - param.offset) * param.frequency
        for section in self:

            if section.start_edge is None:
                converted_start = inner_slice_start = None
            else:
                converted_start = (section.start_edge * multiplier) + offset
                inner_slice_start = int(math.ceil(converted_start))
            
            if section.stop_edge is None:
                converted_stop = inner_slice_stop = None
            else:
                converted_stop = (section.stop_edge * multiplier) + offset
                inner_slice_stop = int(math.ceil(converted_stop))

            inner_slice = slice(inner_slice_start, inner_slice_stop)
            aligned_node.create_section(inner_slice, section.name, 
                                        begin = converted_start,
                                        end = converted_stop)
        return aligned_node
    
    slice_attrgetters = {'start': attrgetter('slice.start'),
                         'stop': attrgetter('slice.stop')}
    
    def _get_condition(self, name=None, within_slice=None, within_use='slice'):
        '''
        Returns a condition function which checks if the element is within
        a slice or has a specified name if they are provided.
        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :param within_use: Which part of the slice to use when testing if it is within_slice. Either entire 'slice', or the slice's 'start' or 'stop'.
        :type within_use: str
        :returns: Either a condition function or None.
        :rtype: func or None
        '''
        # Function for testing if Section is within a slice depending on
        # within_use.
        if within_slice:
            within_funcs = \
                {'slice': lambda s, within: is_slice_within_slice(s.slice,
                                                                  within),
                 'start': lambda s, within: is_index_within_slice(s.slice.start,
                                                                  within),
                 'stop': lambda s, within: is_index_within_slice(s.slice.stop,
                                                                 within),
                 'any': lambda s, within: slices_overlap(s.slice, within)}
            within_func = within_funcs[within_use]
        
        if within_slice and name:
            return lambda e: within_func(e, within_slice) and \
                   e.name == name
        elif within_slice:
            return lambda e: within_func(e, within_slice)
        elif name:
            return lambda e: e.name == name
        else:
            return None
    
    def get(self, name=None, within_slice=None, within_use='slice'):
        '''
        Gets elements either within_slice or with name. Duplicated from
        FormattedNameNode. TODO: Share implementation with NameFormattedNode,
        slight differences between types make it difficult.
        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :param within_use: Which part of the slice to use when testing if it is within_slice. Either entire 'slice', or the slice's 'start' or 'stop'.
        :type within_use: str
        :returns: An object of the same type as self containing matching elements.
        :rtype: self.__class__
        '''
        condition = self._get_condition(within_slice=within_slice, name=name,
                                        within_use=within_use)
        matching = filter(condition, self) if condition else self
        return self.__class__(name=self.name, frequency=self.frequency,
                              offset=self.offset, items=matching)
    
    def get_first(self, first_by='start', name=None, within_slice=None,
                  within_use='slice'):
        '''
        :param first_by: Get the first by either 'start' or 'stop' of slice.
        :type first_by: str
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :param within_use: Which part of the slice to use when testing if it is within_slice. Either entire 'slice', or the slice's 'start' or 'stop'.
        :type within_use: str        
        :returns: First Section matching conditions.
        :rtype: Section
        '''
        matching = self.get(within_slice=within_slice, name=name,
                            within_use=within_use)
        if matching:
            return min(matching, key=self.slice_attrgetters[first_by])
        else:
            return None
    
    def get_last(self, last_by='start', name=None, within_slice=None,
                 within_use='slice'):
        '''
        :param last_by: Get the last by either 'start' or 'stop' of slice.
        :type last_by: str        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :param within_use: Which part of the slice to use when testing if it is within_slice. Either entire 'slice', or the slice's 'start' or 'stop'.
        :type within_use: str         
        :returns: Last Section matching conditions.
        :rtype: Section
        '''
        matching = self.get(within_slice=within_slice, name=name,
                            within_use=within_use)
        if matching:
            return max(matching, key=self.slice_attrgetters[last_by])
        else:
            return None
    
    def get_ordered_by_index(self, order_by='start', name=None,
                             within_slice=None, within_use='slice'):
        '''
        :param order_by: Index of slice to use when ordering, either 'start' or 'stop'.
        :type order_by: str
        :param index: Order by either 'start' or 'stop' slice index.
        :type index: str
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :param within_use: Which part of the slice to use when testing if it is within_slice. Either entire 'slice', or the slice's 'start' or 'stop'.
        :type within_use: str         
        :returns: An object of the same type as self containing elements ordered by index.
        :rtype: self.__class__
        '''
        matching = self.get(within_slice=within_slice, name=name,
                            within_use=within_use)
        ordered_by_start = sorted(matching,
                                  key=self.slice_attrgetters[order_by])
        return self.__class__(name=self.name, frequency=self.frequency,
                              offset=self.offset, items=ordered_by_start)
    
    def get_next(self, index, frequency=None, use='start', name=None,
                 within_slice=None, within_use='slice'):
        '''
        Gets the section with the next index optionally filter within_slice or
        by name.
        
        :param index: Index to get the next Section from.
        :type index: int or float
        :param frequency: Frequency of index argument.
        :type frequency: int or float
        :param use: Use either 'start' or 'stop' of slice.
        :type use: str        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str 
        :param within_use: Which part of the slice to use when testing if it is within_slice. Either entire 'slice', or the slice's 'start' or 'stop'.
        :type within_use: str         
        :returns: Section with the next index matching criteria.
        :rtype: Section or None      
        '''
        if frequency:
            index = index * (self.frequency / float(frequency))
        ordered = self.get_ordered_by_index(within_slice=within_slice,
                                            name=name, within_use=within_use)
        for elem in ordered:
            if getattr(elem.slice, use) > index:
                return elem
        return None
    
    def get_previous(self, index, frequency=None, use='stop', within_slice=None,
                     name=None, within_use='slice'):
        '''
        Gets the element with the previous index optionally filter within_slice
        or by name.
        
        :param index: Index to get the previous Section from.
        :type index: int or float
        :param frequency: Frequency of index argument.
        :type frequency: int or float
        :param use: Use either 'start' or 'stop' of slice.
        :type use: str
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str 
        :returns: Element with the previous index matching criteria.
        :rtype: item within self or None     
        '''
        if frequency:
            index = index * (self.frequency / float(frequency))
        ordered = self.get_ordered_by_index(within_slice=within_slice,
                                            name=name, within_use=within_use)
        for elem in reversed(ordered):
            if getattr(elem.slice, use) < index:
                return elem
        return None
    
    def get_surrounding(self, index):
        '''
        Returns a list of sections where the index is surrounded by the
        section's slice.
        
        :param index: The index being inspected
        :type index: float
        :returns: List of surrounding sections
        :rtype: List of sections
        '''
        surrounded = []
        for section in self:
            if section.slice.start <= index <= section.slice.stop or\
               section.slice.start <= index and section.slice.stop is None or\
               section.slice.start is None and index <= section.slice.stop:
                surrounded.append(section)
        return self.__class__(name=self.name, frequency=self.frequency,
                              offset=self.offset, items=surrounded)
    
    def get_slices(self):
        '''
        :returns: A list of slices from sections.
        :rtype: [slice]
        '''
        return [section.slice for section in self]
    

class FlightPhaseNode(SectionNode):
    '''
    Is a Section, but called "phase" for user-friendliness!
    '''
    # create_phase and create_phases are shortcuts for create_section and 
    # create_sections.
    create_phase = SectionNode.create_section
    create_phases = SectionNode.create_sections


class FormattedNameNode(Node, list):
    '''
    NAME_FORMAT example: 
    'Speed in %(phase)s at %(altitude)d ft'

    NAME_VALUES example:
    {'phase'    : ['ascent', 'descent'],
     'altitude' : [1000,1500],}
    '''
    NAME_FORMAT = ""
    NAME_VALUES = {}
    
    def __init__(self, *args, **kwargs):
        '''
        If the there is not an 'items' kwarg and the first argument is a list 
        or a tuple, the first argument's items will be extended to the node.
        
        :param items: Optional keyword argument of initial items to be contained within self.
        :type items: list
        '''
        if 'items' in kwargs:
            self.extend(kwargs['items'])
            del kwargs['items']
            super(FormattedNameNode, self).__init__(*args, **kwargs)
        elif len(args) and (isinstance(args[0], list) or isinstance(args[0],
                                                                    tuple)):
            self.extend(args[0])
            super(FormattedNameNode, self).__init__(*args[1:], **kwargs)
        else:
            super(FormattedNameNode, self).__init__(*args, **kwargs)
        
    def __repr__(self):
        return '%s' % list(self)
    
    @classmethod
    def names(cls):
        """        
        :returns: The product of all NAME_VALUES name combinations
        :rtype: list
        """
        # XXX: cache option below disabled until required. Should we create this
        # on init and remove the property instead?
        ##if hasattr(cls, 'names'):
            ##return cls.names
        if not cls.NAME_FORMAT and not cls.NAME_VALUES:
            return [cls.get_name()]
        names = []
        for a in product(*cls.NAME_VALUES.values()): 
            name = cls.NAME_FORMAT % dict(zip(cls.NAME_VALUES.keys(), a))
            names.append(name)
        ##cls.names = names  #cache
        return names
    
    def _validate_name(self, name):
        """
        Test that name is a valid combination of NAME_FORMAT and NAME_VALUES.
        
        :type name: str
        :rtype: bool
        """
        return name in self.names()
    
    def format_name(self, replace_values={}, **kwargs):
        """
        Formats NAME_FORMAT with interpolation values and returns a KPV object
        with index and value.
        
        Interpolation values not in FORMAT_NAME are ignored.        
        
        :type replace_values: dict
        :raises KeyError: if required interpolation/replace value not provided.
        :raises TypeError: if interpolation value is of wrong type.
        :raises ValueError: If name is not a combination of self.NAME_FORMAT and self.NAME_VALUES.
        :rtype: str
        """
        if not replace_values and not kwargs and not self.NAME_FORMAT:
            # have not defined name format to use, so create using name of node
            return self.get_name()
        rvals = replace_values.copy()  # avoid re-using static type
        rvals.update(kwargs)
        name = self.NAME_FORMAT % rvals  # common error is to use { inplace of (
        # validate name is allowed
        if not self._validate_name(name):
            raise ValueError("invalid name '%s'" % name)
        return name # return as a confirmation it was successful
    
    def _get_condition(self, within_slice=None, name=None):
        '''
        Returns a condition function which checks if the element is within
        a slice or has a specified name if they are provided.
        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :returns: Either a condition function or None.
        :rtype: func or None
        '''
        
        if within_slice and name:
            return lambda e: is_index_within_slice(e.index, within_slice) and \
                   e.name == name
        elif within_slice:
            return lambda e: is_index_within_slice(e.index, within_slice)
        elif name:
            if name not in self.names():
                raise ValueError("Attempted to filter by invalid name '%s' "
                                 "within '%s'." % (name,
                                                   self.__class__.__name__))            
            return lambda e: e.name == name
        else:
            return None
    
    def get(self, within_slice=None, name=None):
        '''
        Gets elements either within_slice or with name.
        
        Q: Could we get by name values rather than formatted name? For example
        .get(name_values={'altitude': 20}) rather than
        .get(name='20 Ft Descending').
        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :returns: An object of the same type as self containing elements ordered by index.
        :rtype: self.__class__
        '''
        condition = self._get_condition(within_slice=within_slice, name=name)
        matching = filter(condition, self) if condition else self
        return self.__class__(name=self.name, frequency=self.frequency,
                              offset=self.offset, items=matching)
    
    def get_ordered_by_index(self, within_slice=None, name=None):
        '''
        Gets elements ordered by index (ascending) optionally filter 
        within_slice or by name.
        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :returns: An object of the same type as self containing elements ordered by index.
        :rtype: self.__class__
        '''
        matching = self.get(within_slice=within_slice, name=name)
        ordered_by_index = sorted(matching, key=attrgetter('index'))
        return self.__class__(name=self.name, frequency=self.frequency,
                              offset=self.offset, items=ordered_by_index)
    
    def get_first(self, within_slice=None, name=None):
        '''
        Gets the element with the lowest index optionally filter within_slice or
        by name.
        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :returns: First element matching conditions.
        :rtype: item within self or None
        '''
        matching = self.get(within_slice=within_slice, name=name)
        if matching:
            return min(matching, key=attrgetter('index')) if matching else None
        else:
            return None
    
    def get_last(self, within_slice=None, name=None):
        '''
        Gets the element with the lowest index optionally filter within_slice or
        by name.
        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :returns: Element with the lowest index matching criteria.
        :rtype: item within self or None
        '''
        matching = self.get(within_slice=within_slice, name=name)
        if matching:
            return max(matching, key=attrgetter('index')) if matching else None
        else:
            return None
    
    def get_next(self, index, frequency=None, within_slice=None, name=None):
        '''
        Gets the element with the next index optionally filter within_slice or
        by name.
        
        :param index: Index to get the next item from.
        :type index: int or float
        :param frequency: Frequency of index.
        :type frequency: int or float
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str 
        :returns: Element with the next index matching criteria.
        :rtype: item within self or None      
        '''
        if frequency:
            index = index * (self.frequency / float(frequency))
        ordered = self.get_ordered_by_index(within_slice=within_slice,
                                            name=name)
        for elem in ordered:
            if elem.index > index:
                return elem
        return None
    
    def get_previous(self, index, frequency=None, within_slice=None, name=None):
        '''
        Gets the element with the previous index optionally filter within_slice
        or by name.
        
        :param index: Index to get the previous item from.
        :type index: int or float
        :param frequency: Frequency of index.
        :type frequency: int or float                
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str 
        :returns: Element with the previous index matching criteria.
        :rtype: item within self or None     
        '''
        if frequency:
            index = index * (self.frequency / float(frequency))      
        ordered = self.get_ordered_by_index(within_slice=within_slice,
                                            name=name)
        for elem in reversed(ordered):
            if elem.index < index:
                return elem
        return None


class KeyTimeInstanceNode(FormattedNameNode):
    def __init__(self, *args, **kwargs):
        # place holder
        super(KeyTimeInstanceNode, self).__init__(*args, **kwargs)
        
    def create_kti(self, index, replace_values={}, **kwargs):
        '''
        Creates a KeyTimeInstance with the supplied index and creates a name
        from applying a combination of replace_values and kwargs as string
        formatting arguments to self.NAME_FORMAT. The KeyTimeInstance is
        appended to self.
        
        :param index: Index of the KeyTimeInstance within the data relative to self.frequency.
        :type index: int or float # Q: is float correct?
        :param replace_values: Dictionary of string formatting arguments to be applied to self.NAME_FORMAT.
        :type replace_values: dict
        :param kwargs: Keyword arguments will be applied as string formatting arguments to self.NAME_FORMAT.
        :type kwargs: dict
        :returns: The created KeyTimeInstance which is now appended to self.
        :rtype: KeyTimeInstance named tuple
        :raises KeyError: If a required string formatting key is not provided.
        :raises TypeError: If a string formatting argument is of the wrong type.
        '''
        if index is None:
            # This is treated as an error because conditions where a KTI does
            # not arise, or where the data is masked at the point of the KTI,
            # should be handled within the calling procedure.
            raise ValueError("Cannot create at index None")
        
        name = self.format_name(replace_values, **kwargs)
        kti = KeyTimeInstance(index, name)
        self.append(kti)
        return kti
    
    def create_ktis_at_edges(self, array, direction='rising_edges', phase=None,
                             name=None, replace_values={}):
        '''
        Create one or more key time instances where a parameter rises or
        falls. Usually used with discrete parameters, e.g. Event marker
        pressed, it is suitable for multi-state or analogue parameters such
        as flap selections.
        
        :param array: The input array.
        :type array: A recorded or derived parameter.
        :param direction: Keyword argument.
        :type direction: string
        :param phase: An optional flight phase (section) argument.
        
        Direction has possible fields 'rising_edges', 'falling_edges' or
        'all_edges'. In the absence of a direction parameter, the default is
        'rising_edges'.
        
        Where phase is supplied, only edges arising within this phase will be
        triggered.
        '''
        
        # Low level function that finds edges from array and creates KTIs
        def kti_edges(array, _slice):
            edge_list = find_edges(array, _slice, direction=direction)
            for edge_index in edge_list:
                if name:
                    # Annotate the transition with the post-change state.
                    self.create_kti(edge_index, replace_values=replace_values,
                                    **{name:array[edge_index+1]})
                else:
                    self.create_kti(edge_index, replace_values=replace_values)
            return
        
        # High level function scans phase blocks or complete array and
        # presents appropriate arguments for analysis. We test for phase.name
        # as phase returns False.
        if phase is None:
            kti_edges(array, slice(0, len(array) + 1))
        else:
            for each_period in phase:
                kti_edges(array, each_period.slice)
        return    

    def create_ktis_on_state_change(self, state, array, change='entering',
                                    phase=None):
        '''
        Create KTIs from multistate parameters where data reaches and leaves
        given state.

        Its logic operates on string representation of the multistate
        parameter, not on the raw data value.

        ..todo: instead of working on the strings in numpy, we need to find the
            numeric value by reversing the mapping.
        '''
        # Low level function that finds start and stop indices of given state
        # and creates KTIs
        def state_changes(state, array, change, _slice=None):
            # TODO: to improve performance reverse the state into numeric value
            # and look it up in array.raw instead
            if _slice is None:
                _slice = slice(0, len(array))
            state_periods = np.ma.clump_unmasked(
                np.ma.masked_not_equal(array[_slice].raw,
                                       array.get_state_value(state)))
            slice_len = len(array[_slice])
            for period in state_periods:
                # Calculate the location in the array
                start = period.start + _slice.start
                stop = period.stop + _slice.start
                if change in ('entering', 'entering_and_leaving') \
                   and period.start > 0:
                    # We don't create the KTI at the beginning of the data, as
                    # it is not a "state change"
                    self.create_kti(start - 0.5)
                if change in ('leaving', 'entering_and_leaving') \
                   and period.stop < slice_len:
                    self.create_kti(stop - 0.5)
            return

        # High level function scans phase blocks or complete array and
        # presents appropriate arguments for analysis. We test for phase.name
        # as phase returns False.
        if phase is None:
            state_changes(state, array, change)
        else:
            for each_period in phase:
                state_changes(state, array, change, each_period.slice)
        return

    def get_aligned(self, param):
        '''
        :param param: Node to align this KeyTimeInstanceNode to.
        :type param: Node subclass
        :returns: An copy of the KeyTimeInstanceNode with its contents aligned to the frequency and offset of param.
        :rtype: KeyTimeInstanceNode
        '''
        multiplier = param.frequency / self.frequency
        offset = (self.offset - param.offset) * param.frequency
        aligned_node = self.__class__(self.name, param.frequency,
                                      param.offset) 
        for kti in self:
            aligned_kti = copy.copy(kti)
            index_aligned = (kti.index * multiplier) + offset
            aligned_kti.index = index_aligned
            aligned_node.append(aligned_kti)
        return aligned_node


class KeyPointValueNode(FormattedNameNode):
    def __init__(self, *args, **kwargs):
        super(KeyPointValueNode, self).__init__(*args, **kwargs)
    
    @staticmethod
    def _get_slices(slices):
        '''
        If slices is a list of Sections, return the slices from within the
        Sections.
        
        :param slices: Either a list of Sections or a list of slices.
        :type slices: [Section] or [slice]
        :returns: A list of slices.
        :rtype: [slices]
        '''
        return [s.slice if isinstance(s, Section) else s for s in slices]

    def create_kpv(self, index, value, replace_values={}, **kwargs):
        '''
        Creates a KeyPointValue with the supplied index and value, and creates
        a name from applying a combination of replace_values and kwargs as 
        string formatting arguments to self.NAME_FORMAT. The KeyPointValue is
        appended to self.

        :param index: Index of the KeyTimeInstance within the data relative to self.frequency.
        :type index: float (NB data may be interpolated hence use of float here)
        :param value: Value sourced at the index.
        :type value: float
        :param replace_values: Dictionary of string formatting arguments to be applied to self.NAME_FORMAT.
        :type replace_values: dict
        :param kwargs: Keyword arguments will be applied as string formatting arguments to self.NAME_FORMAT.
        :type kwargs: dict
        :returns: The created KeyPointValue which is now appended to self.
        :rtype: KeyTimeInstance named tuple
        :raises KeyError: If a required string formatting key is not provided.
        :raises TypeError: If a string formatting argument is of the wrong type.

        TODO: Add examples using interpolation values as kwargs.
        '''
        # There are a number of algorithms which return None for valid
        # computations, so these conditions are only logged as info...
        if index is None or value is None:
            msg = "'%s' cannot create KPV for index '%s' and value '%s'."
            logger.info(msg, self.name, index, value)
            return

        # ...however where we should have raised an alert but the specific
        # threshold was masked needs to be a warning as this should not
        # happen.
        if value is np.ma.masked:
            msg = "'%s' cannot create KPV at index '%s': Value is masked."
            logger.warn(msg, self.name, index)
            return

        value = float(value)

        # We also should not create KPVs with infinite values as they don't
        # really mean anything and cannot provide useful information.
        if math.isinf(value):
            msg = "'%s' cannot create KPV at index '%s': Value is infinite."
            logger.error(msg, self.name, index)
            return

        # And we also shouldn't create KPVs where the value is not a number as
        # it causes other things to fail and should not happen anyway.
        if math.isnan(value):
            msg = "'%s' cannot create KPV at index '%s': Value is NaN."
            logger.error(msg, self.name, index)
            return

        name = self.format_name(replace_values, **kwargs)
        kpv = KeyPointValue(index, value, name)
        self.append(kpv)
        self.debug('KPV %s' % kpv)
        return kpv

    def get_aligned(self, param):
        '''
        :param param: Node to align this KeyPointValueNode to.
        :type param: Node subclass
        :returns: An copy of the KeyPointValueNode with its contents aligned to the frequency and offset of param.
        :rtype: KeyPointValueNode
        '''
        multiplier = param.frequency / self.frequency
        offset = (self.offset - param.offset) * param.frequency
        aligned_node = self.__class__(self.name, param.frequency, param.offset)
        for kpv in self:
            aligned_kpv = copy.copy(kpv)
            aligned_kpv.index = (aligned_kpv.index * multiplier) + offset
            aligned_node.append(aligned_kpv)
        return aligned_node
    
    def get_max(self, within_slice=None, name=None):
        '''
        Gets the KeyPointValue with the maximum value optionally filter
        within_slice or by name.
        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :rtype: KeyPointValue
        '''
        matching = self.get(within_slice=within_slice, name=name)
        if matching:
            return max(matching, key=attrgetter('value')) if matching else None
        else:
            return None
    
    def get_min(self, within_slice=None, name=None):
        '''
        Gets the KeyPointValue with the minimum value optionally filter
        within_slice or by name.
        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :rtype: KeyPointValue
        '''
        matching = self.get(within_slice=within_slice, name=name)
        if matching:
            return min(matching, key=attrgetter('value')) if matching else None
        else:
            return None
    
    def get_ordered_by_value(self, within_slice=None, name=None):
        '''
        Gets the element with the maximum value optionally filter within_slice
        or by name.
        
        :param within_slice: Only return elements within this slice.
        :type within_slice: slice
        :param name: Only return elements with this name.
        :type name: str
        :rtype: KeyPointValueNode
        '''
        matching = self.get(within_slice=within_slice, name=name)
        ordered_by_value = sorted(matching, key=attrgetter('value'))
        return KeyPointValueNode(name=self.name, frequency=self.frequency,
                                 offset=self.offset, items=ordered_by_value)
    
    def create_kpvs_at_ktis(self, array, ktis, suppress_zeros=False):
        '''
        Creates KPVs by sourcing the array at each KTI index. Requires the array
        to be aligned to the KTIs.
        
        :param array: Array to source values from.
        :type array: np.ma.masked_array
        :param ktis: KTIs with indices to source values within the array from.
        :type ktis: KeyTimeInstanceNode
        :param suppress_zeros: Optional flag to prevent zero values creating a KPV.
        :type suppress_zeros: Boolean, default=False.
    
        :returns None:
        :rtype: None
        '''
        for kti in ktis:
            value = value_at_index(array, kti.index)
            if (not suppress_zeros) or value:
                self.create_kpv(kti.index, value)
                
    create_kpvs_at_kpvs = create_kpvs_at_ktis # both will work the same!
    
    def create_kpvs_within_slices(self, array, slices, function, **kwargs):
        '''
        Shortcut for creating KPVs from a number of slices by retrieving an
        index and value from function (for instance max_value).
        
        :param array: Array to source values from.
        :type array: np.ma.masked_array
        :param slices: Slices to create KPVs within.
        :type slices: SectionNode or list of slices.
        :param function: Function which will return an index and value from the array.
        :type function: function
        :returns: None
        :rtype: None
        '''
        slices = self._get_slices(slices)
        for slice_ in slices:
            index, value = function(array, slice_)
            self.create_kpv(index, value, **kwargs)

    def create_kpv_from_slices(self, array, slices, function, **kwargs):
        '''
        Shortcut for creating a single KPV from multiple slices.
        
        :param array: Array of source data.
        :type array: np.ma.masked_array
        :param slices: Slices from which to create KPVs.
        :type slices: SectionNode or list of slices.
        :param function: Function which will return an index and value from the array.
        :type function: function
        :raises ValueError: If a slice has a step which is not either 1 or None.
        :returns: None
        :rtype: None
        '''
        slices = self._get_slices(slices)
        if not all(s.step in (1, None) for s in slices):
            raise ValueError('Slices must have a step of 1 in '
                             'create_kpv_from_slices.')
        arrays = [array[s] for s in slices]
        # Trap for empty arrays or no slices to scan.
        if not arrays:
            return
        joined_array = np.ma.concatenate(arrays)
        index, value = function(joined_array)
        if index is None:
            
            return
        # Find where the joined_array index is in the original array.
        for _slice in slices:
            start = _slice.start or 0
            stop = _slice.stop or len(array)
            slice_duration = (stop - start)
            if index < slice_duration:
                index += start or 0
                break
            index -= slice_duration
        self.create_kpv(index, value, **kwargs)

    def create_kpv_outside_slices(self, array, slices, function, **kwargs):
        '''
        Shortcut for creating a KPV excluding values within provided slices or
        sections by retrieving an index and value from function (for instance
        max_value).
        
        :param array: Array to source values from.
        :type array: np.ma.masked_array
        :param slices: Slices to exclude from KPV creation.
        :type slices: SectionNode or list of slices.
        :param function: Function which will return an index and value from the array.
        :type function: function
        :returns: None
        :rtype: None
        '''
        slices = self._get_slices(slices)
        for slice_ in slices:
            if isinstance(slice_, Section): # Use slice within Section.
                slice_ = slice_.slice
            # Exclude the slices we don't want:
            array[slice_] = np.ma.masked
        index, value = function(array)
        self.create_kpv(index, value, **kwargs)

    def create_kpvs_from_slice_durations(self, slices, min_duration=0.0, mark='midpoint', **kwargs):
        '''
        Shortcut for creating KPVs from slices based only on the slice duration.
                
        Note: The min_duration should not be used as short duration events
        are filtered by the time threshold in the Analysis Specification, and
        leaving a zero default allows KPVs to be accumulated below the event
        threshold limit and these are useful for changing thresholds in the
        future. The facility is only intended for systems with continuous
        nuisance levels of operation which would swamp the database if not
        filtered before creating the KPV.

        :param slices: Slices from which to create KPVs. Note: as the only
                       parameter they will default to 1Hz.
        :type slices: List of slices.
        :param min_duration: Minimum duration for a KPV to be created.
        :type min_duration: float (seconds)
        :param mark: Optional field to select when to identify the KPV.
        :type mark: String from 'start', 'midpoint' or 'end'
 
        :returns: None
        :rtype: None
        '''
        slices = self._get_slices(slices)
        for slice_ in slices:
            if isinstance(slice_, Section): # Use slice within Section.
                duration = slice_.stop_edge - slice_.start_edge
                if duration > min_duration:
                    if mark == 'start':
                        index = slice_.start_edge
                    elif mark == 'end':
                        index = slice_.stop_edge
                    elif mark == 'midpoint':
                        index = (slice_.stop_edge + slice_.start_edge) / 2.0
                    else:
                        raise ValueError("Unrecognised mark '%s' in "
                                         "create_kpvs_from_slice_durations" %
                                         mark)
                    self.create_kpv(index, duration, **kwargs)
            else:
                duration = slice_.stop - slice_.start
                if duration > min_duration:
                    if mark == 'start':
                        index = slice_.start
                    elif mark == 'end':
                        index = slice_.stop
                    elif mark == 'midpoint':
                        index = (slice_.stop + slice_.start) / 2.0
                    else:
                        raise ValueError("Unrecognised mark '%s' in "
                                         "create_kpvs_from_slice_durations" %
                                         mark)
                    self.create_kpv(index, duration, **kwargs)
                

    def create_kpvs_where_state(self, state, array, hz, phase=None,
                                min_duration=0.0):
        '''
        For discrete and multi-state parameters, this detects an event and
        records the duration of each event.
        
        Note: The min_duration should not be used as short duration events
        are filtered by the time threshold in the Analysis Specification, and
        leaving a zero default allows KPVs to be accumulated below the event
        threshold limit and these are useful for changing thresholds in the
        future. The facility is only intended for systems with continuous
        nuisance levels of operation which would swamp the database if not
        filtered before creating the KPV.

        :param array: The input parameter, with data and sample rate
            information.
        :type array: A recorded or derived multistate (discrete) parameter
        :param phase: An optional flight phase (section) argument.
        :param min_duration: An optional minimum duration for the KPV to become
            valid.
        :type min_duration: Float (seconds)
        :name name: Facility for automatically naming the KPV.
        :type name: String

        Where phase is supplied, only edges arising within this phase will be
        triggered.

        ..todo: instead of working on the strings in numpy, we need to find the
            numeric value by reversing the mapping.
        '''
        def find_events(state, subarray, start_index):
            # TODO: to improve performance reverse the state into numeric value
            # and look it up in array.raw instead
            events = np.ma.clump_unmasked(
                np.ma.masked_not_equal(subarray.raw,
                                       subarray.get_state_value(state)))
            for event in events:
                index = event.start
                value = (event.stop - event.start) / hz
                if value >= min_duration:
                    self.create_kpv(index, value)
            return

        # High level function scans phase blocks or complete array and presents
        # appropriate arguments for analysis.

        # Note the test for "if phase is None" rather than just "if phase"
        # because phase=[] for phases that are evaluated but have not
        # occurred in this flight.
        if phase is None:
            # FIXME: np.ma.masked_not_equal does not use Python indexing, so it
            # will not see our mapped values!
            # "full slice" trick solves this problem
            find_events(state, array[:], 0)
        else:
            for each_period in phase:
                to_scan = array[each_period.slice]
                find_events(state, to_scan, each_period.slice.start or 0)
        return


class FlightAttributeNode(Node):
    '''
    Can only store a single value per Node, however the value can be any
    object (dict, list, integer etc). The class name serves as the name of the
    attribute.
    '''
    def __init__(self, *args, **kwargs):
        self.value = None
        super(FlightAttributeNode, self).__init__(*args, **kwargs)
        # FlightAttributeNodes inherit frequency and offset attributes from Node,
        # yet these are not relevant to them. TODO: Change inheritance.
        self.frequency = self.hz = self.sample_rate = None
        self.offset = None
    
    def __repr__(self):
        '''
        :rtype: str
        '''
        return self.name
    
    def __nonzero__(self):
        """
        Set the boolean value of the object depending on it's attriubute
        content.
        
        Note: If self.value is a boolean then evaluation of the object is the
        same as evaluating the content.
        node.value = True
        bool(node) == bool(node.value)
        """
        # 0 is a meaningful value. Check self.value is not False as False == 0.        
        return bool(self.value or (self.value == 0 and self.value is not False)) 
            
    def set_flight_attribute(self, value):
        self.value = value
    set_flight_attr = set_flight_attribute
    
    def get_aligned(self, param):
        """
        Cannot align a flight attribute.
        
        :returns: self
        :rtype: FlightAttributeNode
        """
        return self


class NodeManager(object):
    def __repr__(self):
        return 'NodeManager: x%d nodes in total' % (
            len(self.lfl) + len(self.requested) + len(self.derived_nodes) + 
            len(self.aircraft_info) + len(self.achieved_flight_record))
    
    def __init__(self, start_datetime, lfl, requested, derived_nodes,
                 aircraft_info, achieved_flight_record):
        """
        Storage of parameter keys and access to derived nodes.
        
        :param start_datetime: datetime of start of data file
        :type start_datetime: datetime
        :param lfl: List of parameter names in data file defined by the LFL.
        :type lfl: [str]
        :type requested: [str]
        :type derived_nodes: dict
        :type aircraft_info: dict
        :type achieved_flight_record: dict
        """
        self.start_datetime = start_datetime
        self.lfl = lfl
        self.requested = requested
        self.derived_nodes = derived_nodes
        # Attributes:
        self.aircraft_info = aircraft_info
        self.achieved_flight_record = achieved_flight_record
        
    def keys(self):
        """
        :returns: Ordered list of all Node names stored within the manager.
        :rtype: list of str
        """
        return sorted(list(set(['Start Datetime'] \
                               + self.lfl \
                               + self.derived_nodes.keys() \
                               + self.aircraft_info.keys() \
                               + self.achieved_flight_record.keys())))

    def get_attribute(self, name):
        """
        Get an attribute value from aircraft_info or achieved_flight_record
        dictionaries. If key is None, returns None. If key is present,
        returns an Attribute.
        
        :param name: Attribute name.
        :type name: str
        :returns: Attribute if available.
        :rtype: Attribute object or None
        """
        if name == 'Start Datetime':
            return Attribute(name, value=self.start_datetime)
        elif name in self.aircraft_info:
            return Attribute(name, value=self.aircraft_info[name])
        elif name in self.achieved_flight_record:
            return Attribute(name, value=self.achieved_flight_record[name])
        else:
            return None
    
    def operational(self, name, available):
        """
        Looks up the node by name and returns whether it can operate with the
        available dependencies.
        
        :param name: Name of Node.
        :type name: str
        :param available: Available dependencies to be passed into the derive method of the Node instance.
        :type available: list of str
        :returns: Result of Operational test on parameter.
        :rtype: bool
        """
        if name in self.lfl \
             or self.aircraft_info.get(name) is not None \
             or self.achieved_flight_record.get(name) is not None \
             or name == 'root'\
             or name == 'Start Datetime':
            return True
        elif name in self.derived_nodes:
            # NOTE: Raises "Unbound method" here due to can_operate being
            # overridden without wrapping with @classmethod decorator
            res = self.derived_nodes[name].can_operate(available)
            if not res:
                logger.debug("Derived Node %s cannot operate with available nodes: %s",
                              name, available)
            return res
        else:
            logger.debug("Node '%s' is unavailable", name)
            return False

    def node_type(self, node_name):
        '''
        :param node_name: Name of node to retrieve type for.
        :type node_name: str
        :returns: Base class of node.
        :rtype: class
        :raises KeyError: If the node name cannot be found.
        '''
        node_clazz = self.derived_nodes[node_name]
        # XXX: If we implement multi-inheritance then this may break.
        return node_clazz.__base__
        

# The following acronyms are intended to be used as placeholder values
# for kwargs in Node derive methods. Cannot instantiate Node subclass without 
# implementing derive.
class Attribute(object):
    def __repr__(self):
        return "Attribute '%s' : %s" % (self.name, self.value)
    
    def __init__(self, name, value=None):
        '''
        :type name: str
        '''
        self.name = name
        self.value = value
        self.frequency = self.hz = self.sample_rate = None
        self.offset = None

    def __nonzero__(self):
        """
        Set the boolean value of the object depending on it's attriubute
        content.
        
        Note: If self.value is a boolean then evaluation of the object is the
        same as evaluating the content.
        node.value = True
        bool(node) == bool(node.value)
        
        :rtype: bool
        """
        # 0 is a meaningful value. Check self.value is not False as False == 0.        
        return bool(self.value or (self.value == 0 and self.value is not False))
    
    def get_aligned(self, param):
        '''
        Attributes do not contain data which can be aligned to other parameters.
        Q: If attributes start storing indices rather than time, this will
        require implementing.
        
        :returns: self
        :rtype: FlightAttributeNode        
        '''
        return self


A = Attribute
P = Parameter
S = SectionNode
KPV = KeyPointValueNode
KTI = KeyTimeInstanceNode
