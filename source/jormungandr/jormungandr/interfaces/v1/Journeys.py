# coding=utf-8

#  Copyright (c) 2001-2014, Canal TP and/or its affiliates. All rights reserved.
#
# This file is part of Navitia,
#     the software to build cool stuff with public transport.
#
# Hope you'll enjoy and contribute to this project,
#     powered by Canal TP (www.canaltp.fr).
# Help us simplify mobility and open public transport:
#     a non ending quest to the responsive locomotion way of traveling!
#
# LICENCE: This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# Stay tuned using
# twitter @navitia
# channel `#navitia` on riot https://riot.im/app/#/room/#navitia:matrix.org
# https://groups.google.com/d/forum/navitia
# www.navitia.io

from __future__ import absolute_import, print_function, unicode_literals, division
import logging
from flask import request, g
from flask_restful import abort
from jormungandr import i_manager, app, fallback_modes
from jormungandr.interfaces.parsers import default_count_arg_type
from jormungandr.interfaces.v1.ResourceUri import complete_links
from functools import wraps
from jormungandr.timezone import set_request_timezone
from jormungandr.interfaces.v1.make_links import create_external_link, create_internal_link
from jormungandr.interfaces.v1.errors import ManageError
from collections import defaultdict
from navitiacommon import response_pb2, type_pb2
from jormungandr.utils import date_to_timestamp
from jormungandr.interfaces.v1.serializer import api
from jormungandr.interfaces.v1.decorators import get_serializer
from navitiacommon import default_values
from jormungandr.interfaces.v1.journey_common import JourneyCommon, compute_possible_region
from jormungandr.parking_space_availability.parking_places_manager import ManageParkingPlaces
import six
from navitiacommon.parser_args_type import (
    BooleanType,
    OptionValue,
    UnsignedInteger,
    PositiveInteger,
    DepthArgument,
)
from jormungandr.interfaces.common import add_poi_infos_types, handle_poi_infos
from jormungandr.scenarios import new_default, distributed
from jormungandr.fallback_modes import FallbackModes

f_datetime = "%Y%m%dT%H%M%S"


class add_debug_info(object):
    """
    display info stored in g for the debug

    must be called after the transformation from protobuff to dict
    """

    def __call__(self, f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            objects = f(*args, **kwargs)

            response = objects[0]

            def get_debug():
                if not 'debug' in response:
                    response['debug'] = {}
                return response['debug']

            if hasattr(g, 'errors_by_region'):
                get_debug()['errors_by_region'] = {}
                for region, er in g.errors_by_region.items():
                    get_debug()['errors_by_region'][region] = er.message

            if hasattr(g, 'regions_called'):
                get_debug()['regions_called'] = g.regions_called

            return objects

        return wrapper


class add_journey_href(object):
    def __call__(self, f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            objects = f(*args, **kwargs)
            if objects[1] != 200 or 'journeys' not in objects[0]:
                return objects
            for journey in objects[0]['journeys']:
                args = dict(request.args)
                allowed_ids = {
                    o['stop_point']['id']
                    for s in journey.get('sections', [])
                    if 'from' in s
                    for o in (s['from'], s['to'])
                    if 'stop_point' in o
                }

                if 'region' in kwargs:
                    args['region'] = kwargs['region']
                if "sections" not in journey:  # this mean it's an isochrone...
                    if 'to' not in args:
                        args['to'] = journey['to']['id']
                    if 'from' not in args:
                        args['from'] = journey['from']['id']
                    args['rel'] = 'journeys'
                    journey['links'] = [create_external_link('v1.journeys', **args)]
                elif allowed_ids and 'public_transport' in (s['type'] for s in journey['sections']):
                    # exactly one first_section_mode
                    if any(s['type'].startswith('bss') for s in journey['sections'][:2]):
                        args['first_section_mode[]'] = 'bss'
                    else:
                        args['first_section_mode[]'] = journey['sections'][0].get('mode', 'walking')

                    # exactly one last_section_mode
                    if any(s['type'].startswith('bss') for s in journey['sections'][-2:]):
                        args['last_section_mode[]'] = 'bss'
                    else:
                        args['last_section_mode[]'] = journey['sections'][-1].get('mode', 'walking')

                    args['min_nb_transfers'] = journey['nb_transfers']
                    args['direct_path'] = 'only' if 'non_pt' in journey['tags'] else 'none'
                    args['min_nb_journeys'] = 5
                    args['is_journey_schedules'] = True
                    allowed_ids.update(args.get('allowed_id[]', []))
                    args['allowed_id[]'] = list(allowed_ids)
                    args['_type'] = 'journeys'
                    args['rel'] = 'same_journey_schedules'

                    # Delete arguments that are contradictory to the 'same_journey_schedules' concept
                    if '_final_line_filter' in args:
                        del args['_final_line_filter']
                    if '_no_shared_section' in args:
                        del args['_no_shared_section']

                    journey['links'] = [create_external_link('v1.journeys', **args)]
            return objects

        return wrapper


# add the link between a section and the ticket needed for that section
class add_fare_links(object):
    def __call__(self, f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            objects = f(*args, **kwargs)
            if objects[1] != 200:
                return objects
            if "journeys" not in objects[0]:
                return objects
            ticket_by_section = defaultdict(list)
            if 'tickets' not in objects[0]:
                return objects

            for t in objects[0]['tickets']:
                if "links" in t:
                    for s in t['links']:
                        ticket_by_section[s['id']].append(t['id'])

            for j in objects[0]['journeys']:
                if "sections" not in j:
                    continue
                for s in j['sections']:

                    # them we add the link to the different tickets needed
                    for ticket_needed in ticket_by_section[s["id"]]:
                        s['links'].append(create_internal_link(_type="ticket", rel="tickets", id=ticket_needed))
                    if "ridesharing_journeys" not in s:
                        continue
                    for rsj in s['ridesharing_journeys']:
                        if "sections" not in rsj:
                            continue
                        for rss in rsj['sections']:
                            # them we add the link to the different ridesharing-tickets needed
                            for rs_ticket_needed in ticket_by_section[rss["id"]]:
                                rss['links'].append(
                                    create_internal_link(_type="ticket", rel="tickets", id=rs_ticket_needed)
                                )

            return objects

        return wrapper


class rig_journey(object):
    """
    decorator to rig journeys in order to put back the requested origin/destination in the journeys
    those origin/destination can be changed internally by some scenarios
    (querying external autocomplete service)
    """

    def __call__(self, f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            objects = f(*args, **kwargs)
            response, status, _ = objects
            if status != 200:
                return objects

            if not hasattr(g, 'origin_detail') or not hasattr(g, 'destination_detail'):
                return objects

            for j in response.get('journeys', []):
                if not 'sections' in j:
                    continue
                logging.debug(
                    'for journey changing origin: {old_o} to {new_o}'
                    ', destination to {old_d} to {new_d}'.format(
                        old_o=j.get('sections', [{}])[0].get('from').get('id'),
                        new_o=(g.origin_detail or {}).get('id'),
                        old_d=j.get('sections', [{}])[-1].get('to').get('id'),
                        new_d=(g.destination_detail or {}).get('id'),
                    )
                )
                if g.origin_detail:
                    j['sections'][0]['from'] = g.origin_detail
                if g.destination_detail:
                    j['sections'][-1]['to'] = g.destination_detail

            return objects

        return wrapper


class Journeys(JourneyCommon):
    def __init__(self):
        # journeys must have a custom authentication process

        super(Journeys, self).__init__(output_type_serializer=api.JourneysSerializer)

        parser_get = self.parsers["get"]

        parser_get.add_argument("count", type=default_count_arg_type, help='Fixed number of different journeys')
        parser_get.add_argument("_min_journeys_calls", type=int, hidden=True)
        parser_get.add_argument("_final_line_filter", type=BooleanType(), hidden=True)
        parser_get.add_argument(
            "is_journey_schedules",
            type=BooleanType(),
            default=False,
            help="True when '/journeys' is called to compute"
            "the same journey schedules and "
            "it'll override some specific parameters",
        )
        parser_get.add_argument(
            "min_nb_journeys",
            type=UnsignedInteger(),
            help='Minimum number of different suggested journeys, must be >= 0',
        )
        parser_get.add_argument(
            "max_nb_journeys",
            type=PositiveInteger(),
            help='Maximum number of different suggested journeys, must be > 0',
        )
        parser_get.add_argument("_max_extra_second_pass", type=int, dest="max_extra_second_pass", hidden=True)

        parser_get.add_argument(
            "debug",
            type=BooleanType(),
            default=False,
            hidden=True,
            help='Activate debug mode.\n' 'No journeys are filtered in this mode.',
        )
        parser_get.add_argument(
            "show_codes",
            type=BooleanType(),
            default=False,
            hidden=True,
            deprecated=True,
            help="DEPRECATED, show more identification codes",
        )
        parser_get.add_argument(
            "_override_scenario",
            type=six.text_type,
            hidden=True,
            help="debug param to specify a custom scenario",
        )
        parser_get.add_argument(
            "_street_network", type=six.text_type, hidden=True, help="choose the streetnetwork component"
        )
        parser_get.add_argument("_walking_transfer_penalty", hidden=True, type=int)
        parser_get.add_argument("_max_successive_physical_mode", hidden=True, type=int)
        parser_get.add_argument("_max_additional_connections", hidden=True, type=int)
        parser_get.add_argument("_night_bus_filter_base_factor", hidden=True, type=int)
        parser_get.add_argument("_night_bus_filter_max_factor", hidden=True, type=float)
        parser_get.add_argument("_min_car", hidden=True, type=int)
        parser_get.add_argument("_min_bike", hidden=True, type=int)
        parser_get.add_argument("_min_taxi", hidden=True, type=int)
        parser_get.add_argument(
            "bss_stands",
            type=BooleanType(),
            default=False,
            deprecated=True,
            help="DEPRECATED, Use add_poi_infos[]=bss_stands",
        )
        parser_get.add_argument(
            "add_poi_infos[]",
            type=OptionValue(add_poi_infos_types),
            default=[],
            dest="add_poi_infos",
            action="append",
            help="Show more information about the poi if it's available, for instance, show "
            "BSS/car park availability in the pois(BSS/car park) of response",
        )
        parser_get.add_argument(
            "_no_shared_section",
            type=BooleanType(),
            default=False,
            hidden=True,
            dest="no_shared_section",
            help="Shared section journeys aren't returned as a separate journey",
        )
        parser_get.add_argument(
            "timeframe_duration",
            type=int,
            help="Minimum timeframe to search journeys.\n"
            "For example 'timeframe_duration=3600' will search for all "
            "interesting journeys departing within the next hour.\n"
            "Nota 1: Navitia can return journeys after that timeframe as it's "
            "actually a minimum.\n"
            "Nota 2: 'max_nb_journeys' parameter has priority over "
            "'timeframe_duration' parameter.",
        )
        parser_get.add_argument(
            "_max_nb_crowfly_by_walking",
            type=int,
            hidden=True,
            help="limit nb of stop points accesible by walking crowfly, "
            "used especially in distributed scenario",
        )
        parser_get.add_argument(
            "_max_nb_crowfly_by_car",
            type=int,
            hidden=True,
            help="limit nb of stop points accesible by car crowfly, " "used especially in distributed scenario",
        )
        parser_get.add_argument(
            "_max_nb_crowfly_by_taxi",
            type=int,
            hidden=True,
            help="limit nb of stop points accesible by taxi crowfly, " "used especially in distributed scenario",
        )
        parser_get.add_argument(
            "_max_nb_crowfly_by_bike",
            type=int,
            hidden=True,
            help="limit nb of stop points accesible by bike crowfly, " "used especially in distributed scenario",
        )
        parser_get.add_argument(
            "_max_nb_crowfly_by_bss",
            type=int,
            hidden=True,
            help="limit nb of stop points accesible by bss crowfly, " "used especially in distributed scenario",
        )
        parser_get.add_argument(
            "equipment_details",
            default=True,
            type=BooleanType(),
            help="enhance response with accessibility equipement details",
        )
        for mode in FallbackModes.modes_str():
            parser_get.add_argument(
                "max_{}_direct_path_duration".format(mode),
                type=int,
                help="limit duration of direct path in {}, used ONLY in distributed scenario".format(mode),
            )
        parser_get.add_argument("depth", type=DepthArgument(), default=1, help="The depth of your object")
        args = self.parsers["get"].parse_args()

        self.get_decorators.append(complete_links(self))

        if handle_poi_infos(args["add_poi_infos"], args["bss_stands"]):
            self.get_decorators.insert(1, ManageParkingPlaces(self, 'journeys'))

    @add_debug_info()
    @add_fare_links()
    @add_journey_href()
    @rig_journey()
    @get_serializer(serpy=api.JourneysSerializer)
    @ManageError()
    def get(self, region=None, lon=None, lat=None, uri=None):
        args = self.parsers['get'].parse_args()
        possible_regions = compute_possible_region(region, args)
        args.update(self.parse_args(region, uri))

        # count override min_nb_journey or max_nb_journey
        if 'count' in args and args['count']:
            args['min_nb_journeys'] = args['count']
            args['max_nb_journeys'] = args['count']

        if (
            args['min_nb_journeys']
            and args['max_nb_journeys']
            and args['max_nb_journeys'] < args['min_nb_journeys']
        ):
            abort(400, message='max_nb_journeyes must be >= min_nb_journeys')

        if args.get('timeframe_duration'):
            args['timeframe_duration'] = min(args['timeframe_duration'], default_values.max_duration)

        if args['destination'] and args['origin']:
            api = 'journeys'
        else:
            api = 'isochrone'

        if api == 'isochrone':
            # we have custom default values for isochrone because they are very resource expensive
            if args.get('max_duration') is None:
                args['max_duration'] = app.config['ISOCHRONE_DEFAULT_VALUE']
            if 'ridesharing' in args['origin_mode'] or 'ridesharing' in args['destination_mode']:
                abort(400, message='ridesharing isn\'t available on isochrone')

        def _set_specific_params(mod):
            if args.get('max_duration') is None:
                args['max_duration'] = mod.max_duration
            if args.get('_walking_transfer_penalty') is None:
                args['_walking_transfer_penalty'] = mod.walking_transfer_penalty
            if args.get('_night_bus_filter_base_factor') is None:
                args['_night_bus_filter_base_factor'] = mod.night_bus_filter_base_factor
            if args.get('_night_bus_filter_max_factor') is None:
                args['_night_bus_filter_max_factor'] = mod.night_bus_filter_max_factor
            if args.get('_max_additional_connections') is None:
                args['_max_additional_connections'] = mod.max_additional_connections
            if args.get('min_nb_journeys') is None:
                args['min_nb_journeys'] = mod.min_nb_journeys
            if args.get('max_nb_journeys') is None:
                args['max_nb_journeys'] = mod.max_nb_journeys
            if args.get('_min_journeys_calls') is None:
                args['_min_journeys_calls'] = mod.min_journeys_calls
            if args.get('_max_successive_physical_mode') is None:
                args['_max_successive_physical_mode'] = mod.max_successive_physical_mode
            if args.get('_final_line_filter') is None:
                args['_final_line_filter'] = mod.final_line_filter
            if args.get('max_extra_second_pass') is None:
                args['max_extra_second_pass'] = mod.max_extra_second_pass
            if args.get('additional_time_after_first_section_taxi') is None:
                args['additional_time_after_first_section_taxi'] = mod.additional_time_after_first_section_taxi
            if args.get('additional_time_before_last_section_taxi') is None:
                args['additional_time_before_last_section_taxi'] = mod.additional_time_before_last_section_taxi

            # we create a new arg for internal usage, only used by distributed scenario
            args['max_nb_crowfly_by_mode'] = mod.max_nb_crowfly_by_mode  # it's a dict of str vs int
            for mode in fallback_modes.all_fallback_modes:
                nb_crowfly = args.get('_max_nb_crowfly_by_{}'.format(mode))
                if nb_crowfly is not None:
                    args['max_nb_crowfly_by_mode'][mode] = nb_crowfly

            # activated only for distributed
            for mode in FallbackModes.modes_str():
                tmp = 'max_{}_direct_path_duration'.format(mode)
                if args.get(tmp) is None:
                    args[tmp] = getattr(mod, tmp)

        if region:
            _set_specific_params(i_manager.instances[region])
        else:
            _set_specific_params(default_values)

        # When computing 'same_journey_schedules'(is_journey_schedules=True), some parameters need to be overridden
        # because they are contradictory to the request
        if args.get("is_journey_schedules"):
            # '_final_line_filter' (defined in db) removes journeys with the same lines sequence
            args["_final_line_filter"] = False
            # 'no_shared_section' removes journeys with a section that have the same origin and destination stop points
            args["no_shared_section"] = False

        if not (args['destination'] or args['origin']):
            abort(400, message="you should at least provide either a 'from' or a 'to' argument")

        if args['debug']:
            g.debug = True

        # Add the interpreted parameters to the stats
        self._register_interpreted_parameters(args)
        logging.getLogger(__name__).debug(
            "We are about to ask journeys on regions : {}".format(possible_regions)
        )

        # Store the different errors
        responses = {}
        for r in possible_regions:
            self.region = r

            set_request_timezone(self.region)

            # Store the region in the 'g' object, which is local to a request
            if args['debug']:
                # In debug we store all queried region
                if not hasattr(g, 'regions_called'):
                    g.regions_called = []
                g.regions_called.append(r)

            # Save the original datetime for debuging purpose
            original_datetime = args['original_datetime']
            if original_datetime:
                new_datetime = self.convert_to_utc(original_datetime)
            args['datetime'] = date_to_timestamp(new_datetime)

            scenario_name = i_manager.get_instance_scenario_name(self.region, args.get('_override_scenario'))

            if scenario_name == "new_default" and (
                "taxi" in args["origin_mode"] or "taxi" in args["destination_mode"]
            ):
                abort(400, message="taxi is not available with new_default scenario")

            response = i_manager.dispatch(args, api, instance_name=self.region)

            # If journeys list is empty and error field not exist, we create
            # the error message field
            if not response.journeys and not response.HasField(str('error')):
                logging.getLogger(__name__).debug(
                    "impossible to find journeys for the region {}," " insert error field in response ".format(r)
                )
                response.error.id = response_pb2.Error.no_solution
                response.error.message = "no solution found for this journey"
                response.response_type = response_pb2.NO_SOLUTION

            if response.HasField(str('error')) and len(possible_regions) != 1:

                if args['debug']:
                    # In debug we store all errors
                    if not hasattr(g, 'errors_by_region'):
                        g.errors_by_region = {}
                    g.errors_by_region[r] = response.error

                logging.getLogger(__name__).debug(
                    "impossible to find journeys for the region {},"
                    " we'll try the next possible region ".format(r)
                )
                responses[r] = response
                continue

            non_pt_types = ("non_pt_walk", "non_pt_bike", "non_pt_bss", "car")
            if all(j.type in non_pt_types for j in response.journeys) or all(
                "non_pt" in j.tags for j in response.journeys
            ):
                responses[r] = response
                continue

            if args['equipment_details']:
                # Manage equipments in stop points from the journeys sections
                instance = i_manager.instances.get(self.region)
                return instance.equipment_provider_manager.manage_equipments_for_journeys(response)

            return response

        for response in responses.values():
            if not response.HasField(str("error")):
                return response

        # if no response have been found for all the possible regions, we have a problem
        # if all response had the same error we give it, else we give a generic 'no solution' error
        first_response = list(responses.values())[0]
        if all(r.error.id == first_response.error.id for r in responses.values()):
            return first_response

        resp = response_pb2.Response()
        er = resp.error
        er.id = response_pb2.Error.no_solution
        er.message = "No journey found"

        return resp

    def options(self, **kwargs):
        return self.api_description(**kwargs)
