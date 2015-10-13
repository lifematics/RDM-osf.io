import re
import functools
import operator
from dateutil import parser as date_parser
import datetime

from modularodm import Q
from rest_framework.filters import OrderingFilter
from rest_framework import serializers as ser

from api.base.exceptions import (
    InvalidFilterError,
    InvalidFilterOperator,
    InvalidFilterComparisonType,
    InvalidFilterMatchType,
    InvalidFilterValue,
    InvalidFilterFieldError
)
from api.base import utils

class ODMOrderingFilter(OrderingFilter):
    """Adaptation of rest_framework.filters.OrderingFilter to work with modular-odm."""

    # override
    def filter_queryset(self, request, queryset, view):
        ordering = self.get_ordering(request, queryset, view)
        if ordering:
            return queryset.sort(*ordering)
        return queryset


class FilterMixin(object):
    """ View mixin with helper functions for filtering. """

    QUERY_PATTERN = re.compile(r'^filter\[(?P<field>\w+)\](\[(?P<op>\w+)\])?$')
    COMPARISON_OPERATORS = ('gt', 'gte', 'lt', 'lte')
    MATCH_OPERATORS = ('contains', 'icontains')
    MATCHABLE_FIELDS = (ser.CharField, ser.ListField)
    DEFAULT_OPERATOR = 'eq'
    DEFAULT_OPERATOR_OVERRIDES = {
        ser.CharField: 'icontains',
        ser.ListField: 'contains',
    }

    NUMERIC_FIELDS = (ser.IntegerField, ser.DecimalField, ser.FloatField)

    TIME_FIELDS = (ser.DateTimeField, ser.DateField)
    DATETIME_PATTERN = re.compile(r'^\d{4}\-\d{2}\-\d{2}(?P<time>T\d{2}:\d{2}(:\d{2}(:\d{1,6})?)?)$')

    COMPARABLE_FIELDS = NUMERIC_FIELDS + TIME_FIELDS

    def __init__(self, *args, **kwargs):
        super(FilterMixin, self).__init__(*args, **kwargs)
        if not self.serializer_class:
            raise NotImplementedError()

    def _get_default_operator(self, field):
        return self.DEFAULT_OPERATOR_OVERRIDES.get(type(field), self.DEFAULT_OPERATOR)

    def _get_field_or_error(self, field_name):
        """
        Check that the attempted filter field is valid

        :raises InvalidFilterError: If the filter field is not valid
        """
        if field_name not in self.serializer_class._declared_fields:
            raise InvalidFilterError(detail="'{0}' is not a valid field for this endpoint.".format(field_name))
        if field_name not in getattr(self.serializer_class, 'filterable_fields', {}):
            raise InvalidFilterFieldError(attribute=field_name)
        return self.serializer_class._declared_fields[field_name]

    def _validate_operator(self, field, operator):
        """
        Check that the operator and field combination is valid

        :raises InvalidFilterComparisonType: If the query contains comparisons against non-date or non-numeric fields
        :raises InvalidFilterMatchType: If the query contains comparisons against non-string or non-list fields
        :raises InvalidFilterOperator: If the filter operator is not a member of self.COMPARISON_OPERATORS
        """
        if operator not in set(self.MATCH_OPERATORS + self.COMPARISON_OPERATORS + (self.DEFAULT_OPERATOR, )):
            raise InvalidFilterOperator(value=operator)
        if operator in self.COMPARISON_OPERATORS:
            if type(field) not in self.COMPARABLE_FIELDS:
                raise InvalidFilterComparisonType(attribute=field.field_name)
        if operator in self.MATCH_OPERATORS:
            if type(field) not in self.MATCHABLE_FIELDS:
                raise InvalidFilterMatchType(attribute=field.field_name)

    def _parse_date_param(self, field, field_name, operator, value):
        """
        Allow for ambiguous date filters. This supports operations like findings Nodes created on a given day
        even though Node.date_created is a specific datetime.

        :return list<dict>: list of one (specific datetime) or more (date range) parsed query params
        """
        time_match = self.DATETIME_PATTERN.match(value)
        if operator != 'eq' or time_match:
            return [{
                'op': operator,
                'value': self.convert_value(value, field)
            }]
        else:  # TODO: let times be as generic as possible (i.e. whole month, whole year)
            start = self.convert_value(value, field)
            stop = start + datetime.timedelta(days=1)
            return [{
                'op': 'gte',
                'value': start
            }, {
                'op': 'lt',
                'value': stop
            }]

    def parse_query_params(self, query_params):
        """Maps query params to a dict useable for filtering
        :param dict query_params:
        :return dict: of the format {
            <resolved_field_name>: {
                'op': <comparison_operator>,
                'value': <resolved_value>
            }
        }
        """
        query = {}
        for key, value in query_params.iteritems():
            match = self.QUERY_PATTERN.match(key)
            if match:
                field_name = match.groupdict()['field'].strip()
                field = self._get_field_or_error(field_name)

                op = match.groupdict().get('op') or self._get_default_operator(field)
                self._validate_operator(field, op)

                field_name = self.convert_key(field_name, field)
                if field_name not in query:
                    query[field_name] = []

                # Special case date(time)s to allow for ambiguous date matches
                if type(field) in self.TIME_FIELDS:
                    query[field_name].extend(self._parse_date_param(field, field_name, op, value))
                else:
                    query[field_name].append({
                        'op': op,
                        'value': self.convert_value(value, field)
                    })
        return query

    def convert_key(self, field_name, field):
        """Used so that that queries on fields with the souce attribute set will work
        :param basestring field_name: text representation of the field name
        :param rest_framework.fields.Field field: Field instance
        """
        return field.source or field_name

    def convert_value(self, value, field):
        """Used to convert string values from query params to bools and dates when necessary
        :param basestring value: value to be resolved
        :param rest_framework.fields.Field field: Field instance
        """
        field_type = type(field)
        if field_type == ser.BooleanField:
            if utils.is_truthy(value):
                return True
            elif utils.is_falsy(value):
                return False
            else:
                raise InvalidFilterValue(
                    value=value,
                    field_type='bool'
                )
        elif field_type in self.TIME_FIELDS:
            try:
                return date_parser.parse(value)
            except ValueError:
                raise InvalidFilterValue(
                    value=value,
                    field_type='date'
                )
        elif field_type in self.NUMERIC_FIELDS:
            return float(value)
        else:
            return value.strip()


class ODMFilterMixin(FilterMixin):
    """View mixin that adds a get_query_from_request method which converts query params
    of the form `filter[field_name]=value` into an ODM Query object.

    Subclasses must define `get_default_odm_query()`.

    Serializers that want to restrict which fields are used for filtering need to have a variable called
    filterable_fields which is a frozenset of strings representing the field names as they appear in the serialization.
    """

    # TODO Handle simple and complex non-standard fields
    field_comparison_operators = {
        ser.CharField: 'icontains',
        ser.ListField: 'contains',
    }

    def __init__(self, *args, **kwargs):
        super(FilterMixin, self).__init__(*args, **kwargs)
        if not self.serializer_class:
            raise NotImplementedError()

    def get_default_odm_query(self):
        """Return the default MODM query for the result set.

        NOTE: If the client provides additional filters in query params, the filters
        will intersected with this query.
        """
        raise NotImplementedError('Must define get_default_odm_query')

    def get_query_from_request(self):
        param_query = self.query_params_to_odm_query(self.request.QUERY_PARAMS)
        default_query = self.get_default_odm_query()

        if param_query:
            query = param_query & default_query
        else:
            query = default_query

        return query

    def query_params_to_odm_query(self, query_params):
        """Convert query params to a modularodm Query object."""

        filters = self.parse_query_params(query_params)
        if filters:
            query_parts = []
            for field_name, params in filters.iteritems():
                for group in params:
                    query = Q(field_name, group['op'], group['value'])
                    query_parts.append(query)
            try:
                query = functools.reduce(operator.and_, query_parts)
            except TypeError:
                query = None
        else:
            query = None
        return query


class ListFilterMixin(FilterMixin):
    """View mixin that adds a get_queryset_from_request method which uses query params
    of the form `filter[field_name]=value` to filter a list of objects.

    Subclasses must define `get_default_queryset()`.

    Serializers that want to restrict which fields are used for filtering need to have a variable called
    filterable_fields which is a frozenset of strings representing the field names as they appear in the serialization.
    """
    FILTERS = {
        'eq': operator.eq,
        'lt': operator.lt,
        'lte': operator.le,
        'gt': operator.gt,
        'gte': operator.ge
    }

    def __init__(self, *args, **kwargs):
        super(FilterMixin, self).__init__(*args, **kwargs)
        if not self.serializer_class:
            raise NotImplementedError()

    def get_default_queryset(self):
        raise NotImplementedError('Must define get_default_queryset')

    def get_queryset_from_request(self):
        default_queryset = self.get_default_queryset()
        if self.request.QUERY_PARAMS:
            param_queryset = self.param_queryset(self.request.QUERY_PARAMS, default_queryset)
            return param_queryset
        else:
            return default_queryset

    def param_queryset(self, query_params, default_queryset):
        """filters default queryset based on query parameters"""
        filters = self.parse_query_params(query_params)
        queryset = set(default_queryset)
        if filters:
            for field_name, params in filters.iteritems():
                for group in params:
                    queryset = queryset.intersection(set(self.get_filtered_queryset(field_name, group, default_queryset)))
        return list(queryset)

    def get_filtered_queryset(self, field_name, params, default_queryset):
        """filters default queryset based on the serializer field type"""
        field = self.serializer_class._declared_fields[field_name]
        field_name = field.source or field_name

        if isinstance(field, ser.SerializerMethodField):
            return_val = [
                item for item in default_queryset
                if self.FILTERS[params['op']](self.get_serializer_method(field_name)(item), params['value'])
            ]
        elif isinstance(field, ser.BooleanField):
            return_val = [
                item for item in default_queryset
                if self.FILTERS[params['op']](getattr(item, field_name, None), params['value'])
            ]
        elif isinstance(field, ser.CharField):
            return_val = [
                item for item in default_queryset
                if params['value'] in getattr(item, field_name, None).lower()
            ]
        else:
            return_val = [
                item for item in default_queryset
                if self.FILTERS[params['op']](getattr(item, field_name, None), params['value'])
            ]

        return return_val

    def get_serializer_method(self, field_name):
        """
        :param field_name: The name of a SerializerMethodField
        :return: The function attached to the SerializerMethodField to get its value
        """
        serializer = self.get_serializer()
        serializer_method_name = 'get_' + field_name
        return getattr(serializer, serializer_method_name)
