import '@testing-library/jest-dom'

import { render, screen } from '@testing-library/react'

import { MarketStructureBadge } from './MarketStructureBadge'
import type { MarketStructureResult } from '../lib/marketStructure'
import type { SwingPivot } from '../lib/swings'

function pivot(kind: 'high' | 'low', price: number): SwingPivot {
  return { barIndex: 1, ts: '2025-01-02T00:00:00+00:00', price, kind }
}

describe('MarketStructureBadge', () => {
  it('renders the structural trend and a glossary chip per present term', () => {
    const structure: MarketStructureResult = {
      structuralTrend: 'up',
      labeledPivots: [
        { pivot: pivot('high', 120), label: 'HH' },
        { pivot: pivot('low', 100), label: 'HL' },
      ],
      events: [{ kind: 'CHoCH', direction: 'bearish', barIndex: 5, price: 95 }],
    }
    render(<MarketStructureBadge structure={structure} />)
    const badge = screen.getByTestId('market-structure-badge')
    expect(badge).toHaveTextContent('Structure')
    expect(badge).toHaveTextContent('Up')
    // A chip per present term (HH, HL present; LH/LL absent), plus the CHoCH event.
    expect(screen.getByText('HH')).toBeInTheDocument()
    expect(screen.getByText('HL')).toBeInTheDocument()
    expect(screen.queryByText('LH')).toBeNull()
    expect(screen.getByText('CHoCH')).toBeInTheDocument()
    expect(screen.queryByText('BOS')).toBeNull()
  })

  it('shows the structural trend independently — range is a value no indicator trend can hold', () => {
    const structure: MarketStructureResult = {
      structuralTrend: 'range',
      labeledPivots: [{ pivot: pivot('high', 120), label: 'HH' }],
      events: [],
    }
    render(<MarketStructureBadge structure={structure} />)
    expect(screen.getByTestId('market-structure-badge')).toHaveTextContent('Range')
  })

  it('renders nothing when the bars carry no confirmed structure', () => {
    const { container } = render(
      <MarketStructureBadge
        structure={{ structuralTrend: 'range', labeledPivots: [], events: [] }}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
