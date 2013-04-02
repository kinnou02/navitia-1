#include "best_trip.h"

namespace navitia { namespace routing {


std::pair<type::idx_t, uint32_t> 
    earliest_trip(const type::JourneyPattern & journey_pattern, const unsigned int order,
                  const navitia::type::DateTime &dt, const type::Data &data, 
                  const type::Properties &required_properties) {

    if(!data.pt_data.stop_points[data.pt_data.journey_pattern_points[journey_pattern.journey_pattern_point_list[order]].stop_point_idx].accessible(required_properties))
        return std::make_pair(type::invalid_idx, 0);


    //On cherche le plus petit stop time de la journey_pattern >= dt.hour()
    std::vector<uint32_t>::const_iterator begin = data.dataRaptor.departure_times.begin() +
            data.dataRaptor.first_stop_time[journey_pattern.idx] +
            order * data.dataRaptor.nb_trips[journey_pattern.idx];
    std::vector<uint32_t>::const_iterator end = begin + data.dataRaptor.nb_trips[journey_pattern.idx];


    auto it = std::lower_bound(begin, end, dt.hour(),
                               [](uint32_t departure_time, uint32_t hour){
                               return departure_time < hour;});
    int idx = it - data.dataRaptor.departure_times.begin();
    auto date = dt.date();

    //On renvoie le premier trip valide
    for(; it != end; ++it) {
        const type::StopTime & st = data.pt_data.stop_times[data.dataRaptor.st_idx_forward[idx]];
        if(data.dataRaptor.validity_patterns[data.dataRaptor.vp_idx_forward[idx]].test(date)
                && st.pick_up_allowed() 
                && data.pt_data.vehicle_journeys[st.vehicle_journey_idx].accessible(required_properties)
                && (!st.is_frequency() || ((st.start_time%raptor::dataRAPTOR::SECONDS_PER_DAY<st.end_time%raptor::dataRAPTOR::SECONDS_PER_DAY) && (st.start_time <= dt.hour() && st.end_time >= dt.hour()))
                                       || ((st.start_time%raptor::dataRAPTOR::SECONDS_PER_DAY>st.end_time%raptor::dataRAPTOR::SECONDS_PER_DAY) && !(st.end_time <= dt.hour() && st.start_time >= dt.hour())))) {
            return std::make_pair(st.vehicle_journey_idx,
                                  !st.is_frequency() ? 0 : compute_gap(dt.hour(), st.start_time, st.headway_secs));
        }
        ++idx;
    }

    //Si on en a pas trouvé, on cherche le lendemain
    ++date;
    idx = begin - data.dataRaptor.departure_times.begin();
    for(it = begin; it != end; ++it) {
        const type::StopTime & st = data.pt_data.stop_times[data.dataRaptor.st_idx_forward[idx]];
        if(data.dataRaptor.validity_patterns[data.dataRaptor.vp_idx_forward[idx]].test(date)
                && st.pick_up_allowed() 
                && data.pt_data.vehicle_journeys[st.vehicle_journey_idx].accessible(required_properties)
                && (!st.is_frequency() || ((st.start_time%raptor::dataRAPTOR::SECONDS_PER_DAY<st.end_time%raptor::dataRAPTOR::SECONDS_PER_DAY) && (st.start_time <= dt.hour() && st.end_time >= dt.hour()))
                                       || ((st.start_time%raptor::dataRAPTOR::SECONDS_PER_DAY>st.end_time%raptor::dataRAPTOR::SECONDS_PER_DAY) && !(st.end_time <= dt.hour() && st.start_time >= dt.hour()))))
            return std::make_pair(st.vehicle_journey_idx,
                                  !st.is_frequency() ? 0 : compute_gap(dt.hour(), st.start_time, st.headway_secs));
        ++idx;
    }

    //Cette journey_pattern ne comporte aucun trip compatible
    return std::make_pair(type::invalid_idx, 0);
}


std::pair<type::idx_t, uint32_t> 
tardiest_trip(const type::JourneyPattern & journey_pattern, const unsigned int order,
              const navitia::type::DateTime &dt, const type::Data &data,
              const type::Properties &required_properties) {
    if(!data.pt_data.stop_points[data.pt_data.journey_pattern_points[journey_pattern.journey_pattern_point_list[order]].stop_point_idx].accessible(required_properties))
        return std::make_pair(type::invalid_idx, 0);
    //On cherche le plus grand stop time de la journey_pattern <= dt.hour()
    const auto begin = data.dataRaptor.arrival_times.begin() +
                       data.dataRaptor.first_stop_time[journey_pattern.idx] +
                       order * data.dataRaptor.nb_trips[journey_pattern.idx];
    const auto end = begin + data.dataRaptor.nb_trips[journey_pattern.idx];

    auto it = std::lower_bound(begin, end, dt.hour(),
                               [](uint32_t arrival_time, uint32_t hour){
                                  return arrival_time > hour;}
                              );

    int idx = it - data.dataRaptor.arrival_times.begin();
    auto date = dt.date();
    //On renvoie le premier trip valide
    for(; it != end; ++it) {
        const type::StopTime & st = data.pt_data.stop_times[data.dataRaptor.st_idx_backward[idx]];
        if(data.dataRaptor.validity_patterns[data.dataRaptor.vp_idx_backward[idx]].test(date)
                && st.drop_off_allowed() 
                && data.pt_data.vehicle_journeys[st.vehicle_journey_idx].accessible(required_properties)
                && (!st.is_frequency() || ((st.start_time%data.dataRaptor.SECONDS_PER_DAY<st.end_time%data.dataRaptor.SECONDS_PER_DAY) && (st.start_time <= dt.hour() && st.end_time >= dt.hour()))
                    || ((st.start_time%data.dataRaptor.SECONDS_PER_DAY>st.end_time%data.dataRaptor.SECONDS_PER_DAY) && !(st.end_time <= dt.hour() && st.start_time >= dt.hour()))))
            return std::make_pair(st.vehicle_journey_idx,
                                  !st.is_frequency() ? 0 : compute_gap(dt.hour(), st.start_time, st.headway_secs));
        ++idx;
    }

    //Si on en a pas trouvé, on cherche la veille
    if(date > 0) {
        --date;
        idx = begin - data.dataRaptor.arrival_times.begin();
        for(it = begin; it != end; ++it) {
            const type::StopTime & st = data.pt_data.stop_times[data.dataRaptor.st_idx_backward[idx]];
            if(data.dataRaptor.validity_patterns[data.dataRaptor.vp_idx_backward[idx]].test(date)
                    && st.drop_off_allowed()
                    && data.pt_data.vehicle_journeys[st.vehicle_journey_idx].accessible(required_properties)
                    && (!st.is_frequency() || ((st.start_time%data.dataRaptor.SECONDS_PER_DAY<st.end_time%data.dataRaptor.SECONDS_PER_DAY) && (st.start_time <= dt.hour() && st.end_time >= dt.hour()))
                        || ((st.start_time%data.dataRaptor.SECONDS_PER_DAY>st.end_time%data.dataRaptor.SECONDS_PER_DAY) && !(st.end_time <= dt.hour() && st.start_time >= dt.hour()))))
                return std::make_pair(st.vehicle_journey_idx,
                                      !st.is_frequency() ? 0 : compute_gap(dt.hour(), st.start_time, st.headway_secs));
            ++idx;
        }
    }

    //Cette journey_pattern ne comporte aucun trip compatible
    return std::make_pair(type::invalid_idx, 0);
}
}}
