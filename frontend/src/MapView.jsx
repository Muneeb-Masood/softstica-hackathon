import { CircleMarker, MapContainer, Marker, Popup, TileLayer, useMapEvents } from "react-leaflet";
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

export default function MapView({ aeds, testPoint, onPickLocation }) {
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
          </Popup>
        </CircleMarker>
      ))}
      {testPoint && (
        <Marker position={[testPoint.lat, testPoint.lon]}>
          <Popup>Test location</Popup>
        </Marker>
      )}
    </MapContainer>
  );
}
