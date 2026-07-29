import PropTypes from 'prop-types';
import {
  Chat,
  ChatSidePanelLayout,
  createDefaultTools,
  DisplayResultsToolType,
} from 'react-instantsearch';
import 'instantsearch.css/components/chat.css';
import ChatItemComponent from './ChatItemComponent';

const PROMPT_SUGGESTIONS = [
  'What are your most valuable cards?',
  'Do you have any Charizard cards?',
  'Show me your top chase cards',
  "What's your best water type card?",
  'How do I get a card?',
];

// The Display Results tool payload ({ intro, groups: [{ results: [{ objectID }] }] })
// has shipped in two server contracts: historically it arrived on the tool *output*,
// but the current live agent delivers it on the tool-call *input*. The default
// react-instantsearch renderer only reads `output`, so it renders nothing against the
// current contract. We wrap the default renderer and hand it whichever side actually
// carries the payload — preferring `output`, so this keeps working once the server
// release that moves the payload back to `output` lands. (Per InstantSearch team.)
const defaultTools = createDefaultTools(ChatItemComponent);
const defaultDisplayResults = defaultTools[DisplayResultsToolType];
const DefaultDisplayResultsLayout = defaultDisplayResults.layoutComponent;

function hasDisplayPayload(value) {
  return (
    Boolean(value) &&
    (Array.isArray(value.groups) || typeof value.intro === 'string')
  );
}

function resolveDisplayPayload(message) {
  if (hasDisplayPayload(message?.output)) return message.output;
  if (hasDisplayPayload(message?.input)) return message.input;
  return message?.output;
}

function DisplayResultsLayout({ message, ...rest }) {
  const normalizedMessage = { ...message, output: resolveDisplayPayload(message) };
  return <DefaultDisplayResultsLayout message={normalizedMessage} {...rest} />;
}

DisplayResultsLayout.propTypes = {
  message: PropTypes.object,
};

const chatTools = {
  [DisplayResultsToolType]: {
    ...defaultDisplayResults,
    layoutComponent: DisplayResultsLayout,
  },
};

function ChatGreeting({ sendMessage }) {
  return (
    <div className="ais-ChatGreeting">
      <h2 className="ais-ChatGreeting-heading">
        I&apos;m the Algolia TCG Card Vending Machine
      </h2>
      <p className="ais-ChatGreeting-subheading">
        Ask me what cards are inside, find out what they&apos;re worth, or claim the card you just received.
      </p>
      <div className="chat-greeting-suggestions">
        {PROMPT_SUGGESTIONS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            className="chat-greeting-suggestion"
            onClick={() => sendMessage({ text: prompt })}
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function ChatAgent({ agentId }) {
  return (
    <Chat
      agentId={agentId}
      layoutComponent={ChatSidePanelLayout}
      itemComponent={ChatItemComponent}
      emptyComponent={ChatGreeting}
      tools={chatTools}
      feedback
    />
  );
}

ChatGreeting.propTypes = {
  sendMessage: PropTypes.func.isRequired,
};

ChatAgent.propTypes = {
  agentId: PropTypes.string.isRequired,
};
