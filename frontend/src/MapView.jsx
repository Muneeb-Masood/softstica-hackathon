import { CircleMarker, MapContainer, Marker, Polyline, Popup, TileLayer, useMapEvents } from "react-leaflet";
import { badgeColor } from "./trustBadge";

const SENTOSA_CENTER = [1.2525, 103.82];

function ClickCatcher({ onPick }) {
  useMapEvents({
    click(e) {
      onPick(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

export default function MapView({ aeds, testPoint, onPickLocation, crowdResult, route }) {
  const bottleneckId = crowdResult?.bottleneck?.aed_id ?? null;
  const bottleneckAed = bottleneckId
    ? aeds.find((a) => a.aed_id === bottleneckId)
    : null;

  return (
    <MapContainer center={SENTOSA_CENTER} zoom={16} style={{ height: "100%", width: "100%" }}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {onPickLocation && <ClickCatcher onPick={onPickLocation} />}
      {aeds.map((aed) => (
        <CircleMarker
          key={aed.aed_id}
          center={[aed.latitude, aed.longitude]}
          radius={7}
          pathOptions={{
            color: badgeColor(aed.trust_badge),
            fillColor: badgeColor(aed.trust_badge),
            fillOpacity: 0.85,
            weight: 2,
          }}
        >
          <Popup>
            <strong>{aed.building_name || aed.aed_id}</strong>
            <br />
            AED ID: {aed.aed_id}
            <br />
            Floor: {aed.floor_level || "unknown"}
            <br />
            {aed.description}
            <br />
            Hours: {aed.operating_hours || "unknown"}
            <br />
            Trust: {aed.trust_badge} ({aed.trust_score})
            {aed.trust_badge_reasons && aed.trust_badge_reasons.length > 0 && (
              <ul className="popup-trust-reasons">
                {aed.trust_badge_reasons.map((reason, i) => (
                  <li key={i}>{reason}</li>
                ))}
              </ul>
            )}
          </Popup>
        </CircleMarker>
      ))}
      {testPoint && (
        <Marker position={[testPoint.lat, testPoint.lon]}>
          <Popup>Test location</Popup>
        </Marker>
      )}

      {route?.straight_line && (
        <Polyline
          positions={route.straight_line}
          pathOptions={{ color: "#9aa0a6", weight: 3, dashArray: "6 8", opacity: 0.9 }}
        >
          <Popup>
            Straight-line baseline (required-baseline lookup, ignores whether this line is
            actually walkable): {Math.round(route.straight_line_distance_m)}m
          </Popup>
        </Polyline>
      )}
      {route?.reachable && route.walking_route && (
        <Polyline
          positions={route.walking_route}
          pathOptions={{ color: "#1a73e8", weight: 4, opacity: 0.9 }}
        >
          <Popup>
            Simulated walking route (not live turn-by-turn navigation): {Math.round(route.walking_distance_m)}m,
            ~{Math.round(route.walking_time_s / 60)} min
          </Popup>
        </Polyline>
      )}

      {crowdResult?.points.map((p, i) => (
        <CircleMarker
          key={`crowd-pt-${i}`}
          center={[p.lat, p.lon]}
          radius={3}
          pathOptions={{
            color: p.winner_aed_id === bottleneckId ? "#e8710a" : "#5f6368",
            fillColor: p.winner_aed_id === bottleneckId ? "#e8710a" : "#5f6368",
            fillOpacity: 0.5,
            weight: 0,
          }}
        >
          <Popup>
            Simulated point (not a real person)
            <br />
            #1 pick: {p.winner_building_name || p.winner_aed_id || "none"}
          </Popup>
        </CircleMarker>
      ))}

      {bottleneckAed && (
        <CircleMarker
          center={[bottleneckAed.latitude, bottleneckAed.longitude]}
          radius={14}
          pathOptions={{
            color: "#e8710a",
            fillColor: "#e8710a",
            fillOpacity: 0.15,
            weight: 3,
          }}
        >
          <Popup>
            <strong>Simulated bottleneck</strong>
            <br />
            {bottleneckAed.building_name || bottleneckAed.aed_id}
            <br />
            Won {crowdResult.bottleneck.win_count}/{crowdResult.n_points} simulated points
          </Popup>
        </CircleMarker>
      )}
    </MapContainer>
  );
}
