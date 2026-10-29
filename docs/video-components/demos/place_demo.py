from cpsim import realcities                      # CityPulseSimulator/src/cpsim/realcities.py: standard library only

city = realcities.load("brussels")                # real commune outlines and main roads (OpenStreetMap)
print(realcities.place(city, "sensor-17", "ixelles"))
print(realcities.place(city, "sensor-17", "ixelles"), "same id, same place")
print(realcities.place(city, "sensor-18", "ixelles"))
