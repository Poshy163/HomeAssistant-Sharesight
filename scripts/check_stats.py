import re

with open(r'custom_components/sharesight/enum.py') as f:
    content = f.read()

blocks = re.split(r'SharesightSensorDescription\(', content)
found = False
for block in blocks[1:]:
    end = block.find(')')
    if end == -1:
        continue
    desc = block[:end]
    has_measurement = 'SensorStateClass.MEASUREMENT' in desc
    has_none_unit = 'native_unit_of_measurement=None' in desc
    if has_measurement and has_none_unit:
        key_match = re.search(r"key=['\"]([^'\"]+)", desc)
        key = key_match.group(1) if key_match else '?'
        print(f"PROBLEM: {key} has MEASUREMENT but no unit")
        found = True

if not found:
    print("All sensors with state_class=MEASUREMENT have a native_unit_of_measurement set.")

# Also check for state_class=None on numeric sensors that have a unit
for block in blocks[1:]:
    end = block.find(')')
    if end == -1:
        continue
    desc = block[:end]
    has_none_state = 'state_class=None' in desc
    has_unit = 'native_unit_of_measurement=CURRENCY_DOLLAR' in desc or 'native_unit_of_measurement=PERCENTAGE' in desc
    if has_none_state and has_unit:
        key_match = re.search(r"key=['\"]([^'\"]+)", desc)
        key = key_match.group(1) if key_match else '?'
        print(f"NOTE: {key} has a unit but state_class=None (no long-term stats)")

print("Check complete.")

