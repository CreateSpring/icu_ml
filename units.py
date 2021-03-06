from pint import UnitRegistry
from pint.unit import UnitsContainer
import pandas as pd

class MedicalUreg(UnitRegistry):

    def __init__(self,medical_uom_defs='config/medical_units.txt',**kwargs):
        super(MedicalUreg, self).__init__(**kwargs)
        self.load_definitions(medical_uom_defs)

    def parse_units(self,units):
        try:
            parsed_units = super(MedicalUreg,self).parse_units(units)
        except:
            parsed_units = super(MedicalUreg,self).parse_units(units.lower())
        return parsed_units

    def same_units(self,unit1,unit2):
        return same_units(unit1,unit2,self)

    def same_dimensionality(self,unit1,unit2):
        return same_dimensionality(unit1,unit2,self)

    def convert_units(self,from_units,to_units,data):
        return convert_units(from_units,to_units,data,self)

    def is_volume(self,units):
        if type(units) is str:
            units = self.parse_units(units)
        return is_volume(units)

    def is_mass(self,units):
        if type(units) is str:
            units = self.parse_units(units)
        return is_mass(units)

    def is_temp(self,units):
        if type(units) is str:
            units = self.parse_units(units)
        return units.dimensionality == UnitsContainer({'[temperature]':1.0})

    def is_rate(self,units):
        if type(units) is str:
            units = self.parse_units(units)
        return units.dimensionality.get('[time]',0) == -1.0


def smart_parse_units(units,ureg):
    try:
        parsed_units = ureg.parse_units(units)
    except:
        parsed_units = ureg.parse_units(units.lower())
    return parsed_units


def same_units(unit1,unit2,ureg):
    return ureg.parse_units(unit1) == ureg.parse_units(unit2)

def same_dimensionality(unit1,unit2,ureg):
    return ureg.parse_units(unit1).dimensionality == ureg.parse_units(unit2).dimensionality

def convert_units(from_units,to_units,data,ureg):
    from_parsed = ureg.parse_units(from_units)
    to_parsed = ureg.parse_units(to_units)
    Q_ = ureg.Quantity
    unit_data = Q_(data,from_parsed)
    np_ar = unit_data.to(to_parsed)
    return pd.Series(np_ar,name=np_ar.name,index=data.index)


def is_volume(units):
    return units.dimensionality == UnitsContainer({'[length]':3.0})

def is_mass(units):
    return units.dimensionality == UnitsContainer({'[mass]':1.0})
