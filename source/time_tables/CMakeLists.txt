add_library(thermometer thermometer.cpp)

SET(TIME_TABLES_SRC passages.cpp route_schedules.cpp departure_boards.cpp request_handle.cpp)
add_library(time_tables ${TIME_TABLES_SRC})
target_link_libraries(time_tables routing thermometer)
add_subdirectory(tests)


