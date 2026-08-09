export default function OnboardingModal({ onClose }) {
  return (
    <div className="modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="onboarding-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="onboarding-title">Welcome — how this tool works</h2>
        <p>
          This is a <strong>simulation and preparedness tool</strong> for AED discovery. It is{" "}
          <strong>not</strong> a live emergency app and does not reflect real-time AED
          availability or working condition.
        </p>
        <p>
          <strong>To use it:</strong> click a location on the map to set a test starting
          point, set a date and time in the sidebar, then press <strong>"Find AEDs"</strong> to
          see a ranked list with plain-language explanations and data-trust badges.
        </p>
        <p>
          From there you can explore how the ranking shifts across the day with the time
          slider, run a simulated crowd scenario, or check which AEDs need registry
          re-verification — each section in the sidebar has a short explainer.
        </p>
        <p className="modal-emergency-note">
          In a real emergency, call your local emergency number immediately.
        </p>
        <button type="button" className="modal-close-button" onClick={onClose} autoFocus>
          Got it, let's start
        </button>
      </div>
    </div>
  );
}
