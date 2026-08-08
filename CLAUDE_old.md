# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Backend (Python/FastAPI)
- Install dependencies: `pip install geopandas osmnx networkx fastapi uvicorn`
- Run development server: `uvicorn main:app --reload`
- Run tests: `pytest tests/`
- Run a single test: `pytest tests/test_specific.py::test_function_name`

### Frontend (React)
- Install dependencies: `npm install`
- Start development server: `npm start`
- Run tests: `npm test`
- Run a single test: `npm test -- --testNamePattern="test description"`

### Data Processing
- Filter AED data to Sentosa bounding box: `python scripts/filter_aeds.py`
- Precompute walking network graph: `python scripts/build_graph.py`

## Architecture Overview

### System Components
The application consists of two primary components communicating via REST API:
1. **Backend Server** (Python/FastAPI): Processes AED data, implements ranking algorithms, and serves API endpoints
2. **Frontend Application** (React): Provides user interface for location selection, time adjustment, and results visualization

### Data Flow
1. Backend loads filtered AED dataset (`sentosa_aeds.geojson`) at startup
2. Walking network graph is precomputed and cached for Sentosa area
3. Frontend sends user parameters (latitude, longitude, date, time) to `/rank` endpoint
4. Backend computes real walking distances, evaluates operating hours, applies trust scoring, and returns ranked AEDs with explanations
5. Frontend displays results on map with trust badges and detailed explanations

### Key Backend Modules
- **Data Loader**: Handles GeoJSON parsing and spatial filtering
- **Hours Parser**: Converts free-text operating hours to open/closed status with confidence scores
- **Trust Scorer**: Evaluates AED location description quality and floor information
- **Graph Engine**: Manages OpenStreetMap walking network and pathfinding
- **Ranking Algorithm**: Combines distance, hours confidence, and trust scores into final ranking
- **Explanation Generator**: Creates plain-language justifications for rankings
- **API Endpoints**: `/rank` (ranked results) and `/aeds` (map-ready AED list with trust badges)

### Frontend Features
- Interactive Map: Leaflet-based visualization of AED locations with filtering
- Time Slider: Adjust ranking based on time-of-day availability
- Results Panel: Ranked AED list with individual scoring breakdowns
- Crowd Simulation: Visualizes bottleneck AEDs from simulated starting points
- Trust Summary: Lists AEDs requiring verification based on low confidence scores

### Safety & Simulation Constraints
- All interfaces must display persistent disclaimer: "THIS IS A SIMULATION TOOL AND NOT LIVE EMERGENCY GUIDANCE"
- Operating status shown as confidence estimates, never guarantees
- No real-time AED availability claims; uses historical registry data only
- Simulated data (crowd starting points, time projections) clearly labeled as such

## Development Phases
Following the plan.md sequence:
1. Data filtering and validation
2. Operating hours parsing implementation
3. Trust score calculation system
4. Walking network graph preprocessing
5. Combined ranking algorithm with weighted scoring
6. Explanation and runner-up reasoning generation
7. FastAPI endpoint creation
8. Core React frontend with map and results display
9. Time slider interface for dynamic ranking
10. Crowd simulation visualization
11. Trust score summary view for data quality insights
12. Documentation and reproducibility package preparation

## Important Files (to be created)
- `backend/main.py`: FastAPI application entry point
- `backend/data_parser.py`: GeoJSON loading and filtering logic
- backend/hours_parser.py: Operating hours parsing functions
- backend/trust_scorer.py: Rule-based trust score calculation
- backend/graph_builder.py: OSMnx walking network processing
- backend/ranking.py: Combined scoring algorithm
- backend/explanations.py: Natural language explanation templates
- frontend/src/App.jsx: Main React application component
- frontend/src/components/MapView.jsx: Leaflet map implementation
- frontend/src/components/ResultsPanel.jsx: Ranked AED display
- frontend/src/components/TimeSlider.jsx: Interactive time adjustment
- frontend/src/components/CrowdSimulation.jsx: Bottleneck visualization
- frontend/src/components/TrustSummary.jsx: Data quality reporting

See plan.md for detailed phase descriptions and deliverable requirements.