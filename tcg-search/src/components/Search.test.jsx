import { vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useSearchBox } from 'react-instantsearch';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { useEvent } from '../context/EventContext';
import Search from './Search';

const { FALLBACK_AGENT_ID } = vi.hoisted(() => ({
  FALLBACK_AGENT_ID: 'fallback-agent-id',
}));

vi.mock('../utilities/algolia', () => ({
  searchClient: {},
  getIndexNames: (eventId) => ({
    primary: `tcg_cards_${eventId}`,
    priceAsc: `tcg_cards_${eventId}_price_asc`,
    priceDesc: `tcg_cards_${eventId}_price_desc`,
  }),
  userToken: 'test-user',
  chatAgentId: FALLBACK_AGENT_ID,
}));

vi.mock('../context/EventContext', () => ({
  useEvent: vi.fn(),
}));

vi.mock('react-instantsearch', () => ({
  InstantSearch: ({ children }) => <div>{children}</div>,
  Configure: () => null,
  Hits: () => null,
  Pagination: () => null,
  PoweredBy: () => null,
  SearchBox: ({ aiMode, onSubmit }) => (
    <form data-testid="search-box-form" onSubmit={(e) => { e.preventDefault(); onSubmit?.(); }}>
      <div data-testid="search-box" data-ai-mode={aiMode ? 'true' : undefined} />
    </form>
  ),
  SortBy: () => null,
  useHits: () => ({ results: { hits: [] } }),
  useInstantSearch: () => ({ indexRenderState: {} }),
  useSearchBox: vi.fn(() => ({ refine: vi.fn(), query: '' })),
  useToggleRefinement: () => ({ value: { isRefined: false, count: 0 }, refine: vi.fn() }),
  useClearRefinements: () => ({ refine: vi.fn(), canRefine: false }),
  useSortBy: () => ({ currentRefinement: 'primary', refine: vi.fn() }),
}));

vi.mock('search-insights', () => ({ default: vi.fn() }));
vi.mock('./Header', () => ({ default: () => null }));
vi.mock('./Hit', () => ({ default: () => null }));
vi.mock('./FilterDropdown', () => ({ default: () => null }));
vi.mock('./FilterToggle', () => ({ default: () => null }));
vi.mock('./Carousel', () => ({
  default: ({ title, filters }) => <div data-testid="carousel" data-title={title} data-filters={filters} />,
}));
vi.mock('./ClaimedCarousel', () => ({ default: () => null }));
vi.mock('./ChatAgent', () => ({
  default: ({ agentId }) => <div data-testid="chat-agent" data-agent-id={agentId} />,
}));

function renderSearch(eventConfig) {
  useEvent.mockReturnValue({ eventConfig, loading: false, error: null });
  render(
    <MemoryRouter initialEntries={['/test-event']}>
      <Routes><Route path="/:eventId" element={<Search />} /></Routes>
    </MemoryRouter>
  );
}

describe('Search — landing sections', () => {
  test('always renders Top 10 carousel', () => {
    renderSearch({ event_id: 'test-event' });
    const carousels = screen.getAllByTestId('carousel');
    expect(carousels[0]).toHaveAttribute('data-title', '⭐ Top 10 Chase Cards');
    expect(carousels[0]).toHaveAttribute('data-filters', 'is_top_10_chase_card:true');
  });

  test('renders only Top 10 carousel when landing_sections is absent', () => {
    renderSearch({ event_id: 'test-event' });
    expect(screen.getAllByTestId('carousel')).toHaveLength(1);
  });

  test('renders additional carousels from landing_sections', () => {
    renderSearch({
      event_id: 'test-event',
      landing_sections: [
        { title: 'Gold Cards', filter: 'is_chase_card:true AND card_type:"Gold"' },
        { title: 'Top Illustration Rare Cards', filter: 'is_chase_card:true AND card_type:"Illustration Rare"' },
      ],
    });
    const carousels = screen.getAllByTestId('carousel');
    expect(carousels).toHaveLength(3);
    expect(carousels[1]).toHaveAttribute('data-title', 'Gold Cards');
    expect(carousels[2]).toHaveAttribute('data-title', 'Top Illustration Rare Cards');
  });

  test('renders landing_sections carousels in order', () => {
    renderSearch({
      event_id: 'test-event',
      landing_sections: [
        { title: 'Section A', filter: 'filter_a:true' },
        { title: 'Section B', filter: 'filter_b:true' },
        { title: 'Section C', filter: 'filter_c:true' },
      ],
    });
    const carousels = screen.getAllByTestId('carousel');
    expect(carousels[1]).toHaveAttribute('data-title', 'Section A');
    expect(carousels[2]).toHaveAttribute('data-title', 'Section B');
    expect(carousels[3]).toHaveAttribute('data-title', 'Section C');
  });
});

describe('Search — SearchBox aiMode', () => {
  test('passes aiMode prop to SearchBox', () => {
    renderSearch({ event_id: 'test-event' });
    expect(screen.getByTestId('search-box')).toHaveAttribute('data-ai-mode', 'true');
  });
});

describe('Search — ChatAgent DOM ordering', () => {
  test('chat-agent renders after search-header and before search-panel', () => {
    renderSearch({ event_id: 'test-event' });
    const header = document.querySelector('.search-header');
    const chatAgent = screen.getByTestId('chat-agent');
    const panel = document.querySelector('.search-panel');
    // DOCUMENT_POSITION_FOLLOWING (4) means the argument follows the reference node
    expect(header.compareDocumentPosition(chatAgent) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(chatAgent.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('Search — event name header', () => {
  test('renders the booth when the event has one', () => {
    renderSearch({ event_id: 'test-event', name: 'eTail Boston 2026', booth: '308' });
    expect(screen.getByText('eTail Boston 2026 (Booth 308)')).toBeInTheDocument();
  });

  test('omits the booth parenthetical entirely when booth is empty', () => {
    renderSearch({ event_id: 'test-event', name: 'RetailClub AI Festival 2026', booth: '' });
    expect(screen.getByText('RetailClub AI Festival 2026')).toBeInTheDocument();
    expect(screen.queryByText(/Booth/)).not.toBeInTheDocument();
  });
});

describe('Search — agentId derivation', () => {
  test('passes eventConfig.agent_id to ChatAgent when present', () => {
    useEvent.mockReturnValue({
      eventConfig: { event_id: 'test-event', agent_id: 'event-specific-agent' },
      loading: false,
      error: null,
    });

    render(<MemoryRouter initialEntries={['/test-event']}><Routes><Route path="/:eventId" element={<Search />} /></Routes></MemoryRouter>);
    expect(screen.getByTestId('chat-agent')).toHaveAttribute('data-agent-id', 'event-specific-agent');
  });

  test('falls back to chatAgentId when eventConfig.agent_id is absent', () => {
    useEvent.mockReturnValue({
      eventConfig: { event_id: 'test-event' },
      loading: false,
      error: null,
    });

    render(<MemoryRouter initialEntries={['/test-event']}><Routes><Route path="/:eventId" element={<Search />} /></Routes></MemoryRouter>);
    expect(screen.getByTestId('chat-agent')).toHaveAttribute('data-agent-id', FALLBACK_AGENT_ID);
  });
});

describe('Search — no-results state', () => {
  let aiButton;

  beforeEach(() => {
    aiButton = document.createElement('button');
    aiButton.className = 'ais-AiModeButton';
    document.body.appendChild(aiButton);
  });

  afterEach(() => {
    document.body.removeChild(aiButton);
  });

  test('renders no-results title and AI prompt', () => {
    renderSearch({ event_id: 'test-event' });
    expect(screen.getByText('No cards found')).toBeInTheDocument();
    expect(screen.getByText(/Try asking the AI/)).toBeInTheDocument();
  });

  test('renders AI Mode button', () => {
    renderSearch({ event_id: 'test-event' });
    expect(screen.getByRole('button', { name: /AI Mode/i })).toBeInTheDocument();
  });

  test('clicking AI Mode button triggers .ais-AiModeButton', () => {
    const clickSpy = vi.spyOn(aiButton, 'click');
    renderSearch({ event_id: 'test-event' });
    fireEvent.click(screen.getByRole('button', { name: /AI Mode/i }));
    expect(clickSpy).toHaveBeenCalledOnce();
  });
});

describe('Search — SearchBox enter-to-chat', () => {
  let aiButton;

  beforeEach(() => {
    aiButton = document.createElement('button');
    aiButton.className = 'ais-AiModeButton';
    document.body.appendChild(aiButton);
  });

  afterEach(() => {
    document.body.removeChild(aiButton);
    vi.mocked(useSearchBox).mockReset();
    vi.mocked(useSearchBox).mockImplementation(() => ({ refine: vi.fn(), query: '' }));
  });

  test('clicks .ais-AiModeButton on submit when query is non-empty', () => {
    vi.mocked(useSearchBox).mockReturnValue({ query: 'pikachu', refine: vi.fn() });
    const clickSpy = vi.spyOn(aiButton, 'click');

    renderSearch({ event_id: 'test-event' });
    fireEvent.submit(screen.getByTestId('search-box-form'));

    expect(clickSpy).toHaveBeenCalledOnce();
  });

  test('does not click .ais-AiModeButton on submit when query is empty', () => {
    vi.mocked(useSearchBox).mockReturnValue({ query: '', refine: vi.fn() });
    const clickSpy = vi.spyOn(aiButton, 'click');

    renderSearch({ event_id: 'test-event' });
    fireEvent.submit(screen.getByTestId('search-box-form'));

    expect(clickSpy).not.toHaveBeenCalled();
  });

  test('does not click .ais-AiModeButton on submit when query is whitespace-only', () => {
    vi.mocked(useSearchBox).mockReturnValue({ query: '   ', refine: vi.fn() });
    const clickSpy = vi.spyOn(aiButton, 'click');

    renderSearch({ event_id: 'test-event' });
    fireEvent.submit(screen.getByTestId('search-box-form'));

    expect(clickSpy).not.toHaveBeenCalled();
  });
});
