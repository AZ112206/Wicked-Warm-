# Requirements Document

## Introduction

The Energy Cost Predictor is a Python tool that estimates residential heating energy costs for a house based on its region, with Massachusetts as the primary target region and generalizability to other regions. The tool models heating energy demand as a function of weather trends (particularly winter outdoor temperatures), converts that demand into cost using fuel- and region-specific pricing, and compares the annual cost of a conventional heating system against a heat-pump system to estimate potential savings. Massachusetts-specific behavior includes a seasonal heat-pump electric delivery rate that applies from November 1 through April 30.

Real-world data is not yet available. The current focus is the algorithm and its structure, so all data sources (weather, pricing, house characteristics, reference actuals) MUST be supplied through pluggable providers. The initial implementation uses placeholder data that mimics the real data structure, and real data can be substituted later without changing the core algorithm. The tool includes a calibration mechanism that tunes model parameters so the predicted annual cost for a designated reference house lands within approximately 1% of that house's known actual cost.

This specification covers the algorithm, its data-source abstractions, the cost and savings calculations, and the calibration/accuracy mechanism. It does not cover acquisition of real-world data, user interface, or persistence beyond what the algorithm requires.

## Glossary

- **Predictor**: The overall system that estimates residential heating energy costs for a house in a given region.
- **Region_Profile**: A configurable set of region-specific parameters including fuel prices, electric rates, seasonal rate rules, and climate baseline. Massachusetts is the primary Region_Profile.
- **Weather_Provider**: A pluggable data source that supplies outdoor temperature data and derived heating demand drivers (for example, heating degree days) for a region and time period.
- **Pricing_Provider**: A pluggable data source that supplies energy unit prices for a region, including electricity ($/kWh) and heating fuels (natural gas $/therm, heating oil $/gallon, propane $/gallon).
- **House_Profile**: The input describing the modeled house, including region, heated area, thermal characteristics, heating system type, and thermostat setpoint.
- **Heating_Demand_Model**: The component that converts weather drivers and House_Profile characteristics into heating energy demand for a period.
- **Cost_Model**: The component that converts heating energy demand into monetary cost using Pricing_Provider rates and Region_Profile rules.
- **Heat_Pump_Model**: The component that models heat-pump energy consumption using a coefficient of performance (COP) that varies with outdoor temperature.
- **Seasonal_Heat_Pump_Rate**: A Massachusetts electric delivery rate reduction that applies to heat-pump electricity consumption from November 1 through April 30.
- **Savings_Comparison**: The output comparing annual cost with a conventional heating system against annual cost with a heat-pump system.
- **Reference_House**: A designated House_Profile with a known actual annual heating cost, used to calibrate and validate the model.
- **Calibration**: The process of adjusting Heating_Demand_Model and Cost_Model parameters so the predicted cost for the Reference_House is within the accuracy target.
- **Accuracy_Target**: The maximum acceptable relative error between predicted and actual annual cost for the Reference_House, set to 1 percent.
- **COP**: Coefficient of performance, the ratio of heat delivered to electrical energy consumed by a heat pump at a given outdoor temperature.
- **HDD**: Heating degree days, a measure of heating demand derived from how far outdoor temperature falls below a base temperature over a period.

## Requirements

### Requirement 1: Predict Annual Heating Cost for a House

**User Story:** As a homeowner analyst, I want to predict a house's annual heating energy cost from its region and characteristics, so that I can estimate what the household will spend on heating.

#### Acceptance Criteria

1. WHEN a valid House_Profile and a heating period with a start date and end date are provided, THE Predictor SHALL compute an estimated total heating energy cost for that period and return it within 5 seconds.
2. THE Predictor SHALL compute the estimated cost as the heating energy demand obtained from the Heating_Demand_Model multiplied by the corresponding unit prices obtained from the Pricing_Provider.
3. WHEN the provided heating period spans a full year of 365 days, THE Predictor SHALL report the estimated cost as an annual total covering that 365-day period.
4. THE Predictor SHALL report the estimated cost as a non-negative numeric value in United States dollars rounded to 2 decimal places.
5. IF a required House_Profile field is missing, THEN THE Predictor SHALL return an error that names the missing field and SHALL NOT return an estimated cost.
6. IF the Heating_Demand_Model or the Pricing_Provider is unavailable, THEN THE Predictor SHALL return an error indicating which dependency was unavailable and SHALL NOT return an estimated cost.
7. IF the provided heating period has an end date earlier than its start date, THEN THE Predictor SHALL return an error indicating the heating period is invalid and SHALL NOT return an estimated cost.

### Requirement 2: Pluggable Data Sources

**User Story:** As a developer, I want all external data to come from substitutable providers, so that I can run the algorithm on placeholder data now and swap in real data later without changing the core algorithm.

#### Acceptance Criteria

1. WHEN the Predictor requires outdoor temperature and heating demand drivers, THE Predictor SHALL obtain them exclusively through a Weather_Provider interface and SHALL NOT read outdoor temperature or heating demand drivers from any other source.
2. WHEN the Predictor requires energy unit prices, THE Predictor SHALL obtain them exclusively through a Pricing_Provider interface and SHALL NOT read energy unit prices from any other source.
3. WHEN the Predictor requires the Reference_House actual cost, THE Predictor SHALL obtain it exclusively through a provider interface and SHALL NOT read the Reference_House actual cost from any other source.
4. WHERE a placeholder data source is configured, THE Predictor SHALL produce estimates using placeholder data that exposes the same interface methods, input parameters, and return field names as the corresponding real data source.
5. WHEN a data source is replaced with another source that exposes the same interface methods, input parameters, and return field names, THE Predictor SHALL produce estimates with zero changes to the source code of the Heating_Demand_Model and zero changes to the source code of the Cost_Model.
6. IF a configured provider returns data in which one or more required fields are absent, null, or empty for the requested region or period, THEN THE Predictor SHALL halt the current estimate, SHALL return an error that names the provider and each missing required field, and SHALL NOT produce a partial or default-valued estimate.
7. IF a configured provider fails to return a response within 30 seconds of a request, THEN THE Predictor SHALL halt the current estimate and SHALL return an error that names the provider and indicates a timeout occurred.

### Requirement 3: Region Configurability

**User Story:** As an analyst, I want region-specific parameters to be configurable, so that I can target Massachusetts now and other regions later.

#### Acceptance Criteria

1. WHEN the Predictor receives a region key, THE Predictor SHALL load the region-specific parameters from the Region_Profile identified by that region key.
2. WHERE the region key is Massachusetts, THE Predictor SHALL apply the Massachusetts Region_Profile including its Seasonal_Heat_Pump_Rate rules.
3. WHEN a House_Profile references a region key that matches an existing Region_Profile, THE Predictor SHALL use the matching Region_Profile for all region-specific prices and rules.
4. IF a House_Profile references a region key for which no Region_Profile exists, THEN THE Predictor SHALL reject the request, return an error message that names the unknown region key, and leave any prior loaded Region_Profile unchanged.
5. IF a House_Profile references a region key that is empty or null, THEN THE Predictor SHALL reject the request and return an error message indicating that the region key is missing.
6. THE Region_Profile SHALL define, for its region, the available heating fuel types and a unit price key for each defined heating fuel type.

### Requirement 4: Weather-Driven Heating Demand Modeling

**User Story:** As an analyst, I want heating demand to be driven by outdoor temperature trends, so that winter temperatures realistically shape predicted heating costs.

#### Acceptance Criteria

1. WHEN the Weather_Provider supplies outdoor temperature data and the House_Profile supplies thermal characteristics for a period, THE Heating_Demand_Model SHALL compute heating energy demand for that period as a function of both inputs.
2. THE Heating_Demand_Model SHALL derive heating demand drivers for each interval from the difference, in degrees, between the House_Profile thermostat setpoint and the Weather_Provider outdoor temperature for that interval, summed across all intervals in the period.
3. WHEN the outdoor temperature for an interval is at or above the House_Profile thermostat setpoint, THE Heating_Demand_Model SHALL contribute exactly zero heating demand for that interval.
4. WHEN the Weather_Provider supplies a period whose per-interval outdoor temperatures are each lower than those of an otherwise identical reference period, THE Heating_Demand_Model SHALL compute a strictly greater total heating demand for that colder period than for the reference period.
5. THE Heating_Demand_Model SHALL express computed heating demand in a single fuel-neutral energy unit before the Cost_Model converts it to a fuel-specific quantity.
6. IF the Weather_Provider supplies no outdoor temperature data for one or more intervals in the period, THEN THE Heating_Demand_Model SHALL exclude those intervals from the heating demand computation and return an indication identifying the intervals with missing data, without aborting computation for intervals that have data.

### Requirement 5: Conventional Heating Cost Calculation

**User Story:** As an analyst, I want to compute the cost of heating with a conventional system, so that I have a baseline to compare against a heat pump.

#### Acceptance Criteria

1. WHEN the House_Profile specifies a conventional heating fuel type, THE Cost_Model SHALL convert heating energy demand into a fuel quantity using the fuel energy content and the system efficiency, where system efficiency is a value greater than 0 and less than or equal to 1.
2. THE Cost_Model SHALL compute conventional heating cost by multiplying the fuel quantity by the fuel unit price from the Pricing_Provider, rounding the resulting cost to 2 decimal places.
3. THE Cost_Model SHALL support natural gas priced per therm, heating oil priced per gallon, propane priced per gallon, and electric resistance priced per kilowatt-hour.
4. IF the House_Profile specifies a fuel type that the Region_Profile does not define, THEN THE Cost_Model SHALL return a descriptive error that names the unsupported fuel type and SHALL NOT return a cost value.
5. IF the system efficiency is less than or equal to 0 or greater than 1, THEN THE Cost_Model SHALL return a descriptive error indicating the system efficiency is out of range and SHALL NOT return a cost value.
6. IF the fuel unit price from the Pricing_Provider is unavailable or is a negative value, THEN THE Cost_Model SHALL return a descriptive error indicating the price is unavailable or invalid and SHALL NOT return a cost value.

### Requirement 6: Heat-Pump Cost Calculation

**User Story:** As an analyst, I want to compute the cost of heating with a heat pump, so that I can evaluate it as an alternative.

#### Acceptance Criteria

1. THE Heat_Pump_Model SHALL compute heat-pump electricity consumption in kilowatt-hours by dividing heating energy demand in kilowatt-hours by the COP applicable to each outdoor temperature interval.
2. WHILE the outdoor temperature falls within a defined temperature interval, THE Heat_Pump_Model SHALL apply the COP assigned to that interval, where each COP is a value between 1.0 and 5.0 inclusive.
3. IF a lower temperature interval is compared to a higher temperature interval, THEN THE Heat_Pump_Model SHALL assign a COP to the lower interval that is less than or equal to the COP assigned to the higher interval.
4. IF the heating energy demand is zero, THEN THE Heat_Pump_Model SHALL compute a heat-pump electricity consumption of zero kilowatt-hours without performing a division.
5. THE Cost_Model SHALL compute heat-pump heating cost by multiplying heat-pump electricity consumption in kilowatt-hours by the applicable electricity unit price obtained from the Pricing_Provider.
6. WHERE the Region_Profile defines a Seasonal_Heat_Pump_Rate, THE Cost_Model SHALL apply the seasonal electric delivery rate to heat-pump electricity consumed from November 1 through April 30 inclusive.
7. WHEN heat-pump electricity is consumed outside the November 1 through April 30 inclusive window, THE Cost_Model SHALL apply the standard electricity rate to that consumption.
8. IF the Pricing_Provider returns no electricity unit price, THEN THE Cost_Model SHALL stop the cost computation, retain any previously computed values without modification, and return an error indication reporting that the price is unavailable.

### Requirement 7: Heat-Pump Savings Comparison

**User Story:** As a homeowner analyst, I want a comparison of annual costs with and without a heat pump, so that I can estimate potential savings.

#### Acceptance Criteria

1. WHEN a valid House_Profile and an annual period of 12 consecutive months are provided, THE Savings_Comparison SHALL report the annual conventional heating cost and the annual heat-pump heating cost, each as a non-negative monetary value rounded to 2 decimal places.
2. THE Savings_Comparison SHALL report the estimated annual savings as the conventional cost minus the heat-pump cost, rounded to 2 decimal places.
3. WHEN the heat-pump cost exceeds the conventional cost, THE Savings_Comparison SHALL report the savings as a negative value equal to the conventional cost minus the heat-pump cost.
4. THE Savings_Comparison SHALL report the conventional cost, the heat-pump cost, and the savings in United States dollars, each expressed within the range 0.00 to 999,999.99 for the cost values.
5. IF the provided House_Profile is missing or invalid, THEN THE Savings_Comparison SHALL reject the request, return no cost or savings values, and return an error indication identifying the invalid House_Profile.
6. IF the provided period is not an annual period of 12 consecutive months, THEN THE Savings_Comparison SHALL reject the request, return no cost or savings values, and return an error indication identifying the invalid period.

### Requirement 8: Calibration to a Reference House

**User Story:** As an analyst, I want to calibrate the model against a reference house with known cost, so that predictions are accurate for real houses.

#### Acceptance Criteria

1. WHEN a Reference_House and its known actual annual cost are provided, THE Calibration SHALL adjust Heating_Demand_Model and Cost_Model parameters to reduce the relative error between predicted and actual annual cost on each iteration until the relative error is less than or equal to the Accuracy_Target of 1 percent or the configured iteration limit of 100 iterations is reached.
2. IF the provided known actual annual cost is less than or equal to 0, THEN THE Calibration SHALL reject the input, perform no parameter adjustment, and return an error indication that the actual annual cost must be greater than 0.
3. WHEN Calibration completes and the Accuracy_Target is met, THE Predictor SHALL produce a predicted annual cost for the Reference_House within the Accuracy_Target of 1 percent relative error.
4. WHEN Calibration completes and the Accuracy_Target is met, THE Predictor SHALL use the calibrated parameters for all subsequent predictions for houses in the same region as the Reference_House.
5. IF Calibration cannot reach the Accuracy_Target within the configured iteration limit of 100 iterations, THEN THE Calibration SHALL return the best achieved relative error, retain the parameter values that produced that best achieved relative error, and return an error indication that the Accuracy_Target was not met.
6. WHEN the Reference_House actual annual cost is required, THE Calibration SHALL obtain it through a provider interface rather than a hard-coded value.
7. IF the provider interface fails to return a Reference_House actual annual cost within a 30 second timeout, THEN THE Calibration SHALL abort, perform no parameter adjustment, and return an error indication that the actual annual cost could not be obtained.

### Requirement 9: Accuracy Reporting

**User Story:** As an analyst, I want the tool to report its accuracy against the reference house, so that I can trust the predictions.

#### Acceptance Criteria

1. WHEN a predicted annual cost and a Reference_House actual annual cost are available, THE Predictor SHALL compute the relative error as the absolute difference between the predicted annual cost and the actual annual cost, divided by the actual annual cost.
2. IF the Reference_House actual annual cost is zero, THEN THE Predictor SHALL report an error indicating that relative error cannot be computed and SHALL NOT report an Accuracy_Target result.
3. WHEN the relative error is computed, THE Predictor SHALL report the relative error as a percentage rounded to 2 decimal places, in the range 0.00 to 100.00 percent or greater.
4. WHEN the relative error is at or below the Accuracy_Target, THE Predictor SHALL report that the Accuracy_Target is met.
5. WHEN the relative error is above the Accuracy_Target, THE Predictor SHALL report that the Accuracy_Target is not met.
