import { useEffect, useState } from 'react';
import { faChevronLeft, faChevronRight, faXmark } from '@fortawesome/free-solid-svg-icons';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { WrappedSlide, WrappedUser } from './api';

/**
 * Slides are authored by the model for a 400x700 portrait phone screen (see
 * generate-slides' prompt). Rather than reflow them, scale the whole frame to
 * whatever the room's screen gives us -- transform keeps the layout the model
 * designed and just makes it bigger.
 */
const SLIDE_W = 400;
const SLIDE_H = 700;

function useSlideScale() {
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const fit = () => {
      const h = (window.innerHeight - 140) / SLIDE_H;
      const w = (window.innerWidth - 200) / SLIDE_W;
      setScale(Math.max(0.5, Math.min(h, w, 1.6)));
    };
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, []);

  return scale;
}

function SlideCarousel({
  data,
  user,
  onExit,
}: {
  data: { slides: Array<WrappedSlide> };
  user?: WrappedUser;
  onExit?: () => void;
}) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const slides = data.slides;
  const scale = useSlideScale();

  const goToPrevious = () =>
    setCurrentIndex((prev) => (prev === 0 ? slides.length - 1 : prev - 1));

  const goToNext = () =>
    setCurrentIndex((prev) => (prev === slides.length - 1 ? 0 : prev + 1));

  // Arrow keys, because nobody wants to aim at a chevron on stage.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') goToPrevious();
      else if (e.key === 'ArrowRight' || e.key === ' ') goToNext();
      else if (e.key === 'Escape') onExit?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [slides.length, onExit]);

  if (slides.length === 0) {
    return null;
  }

  const currentSlide = slides[currentIndex];

  return (
    <main className="deck">
      <header className="deck-bar">
        <span className="deck-who">
          {user ? user.displayName ?? user.handle : ''}
          {user && <span className="deck-handle">@{user.handle}</span>}
        </span>
        {onExit && (
          <button
            type="button"
            className="icon-btn"
            onClick={onExit}
            aria-label="Start over"
          >
            <FontAwesomeIcon icon={faXmark} />
          </button>
        )}
      </header>

      <div className="deck-stage">
        <button
          type="button"
          onClick={goToPrevious}
          aria-label="Previous slide"
          className="icon-btn nav"
        >
          <FontAwesomeIcon icon={faChevronLeft} />
        </button>

        <div
          className="slide-frame"
          style={{
            width: SLIDE_W * scale,
            height: SLIDE_H * scale,
          }}
        >
          <div
            className="slide-preview"
            style={{ transform: `scale(${scale})` }}
          >
            {currentSlide.html ? (
              // Safe only because this HTML comes from our own Bedrock call
              // with our own prompt. Never render user-supplied HTML here.
              <div
                key={currentSlide.slideType}
                className="slide-html"
                dangerouslySetInnerHTML={{ __html: currentSlide.html }}
              />
            ) : (
              <div key={currentSlide.slideType} className="slide-html empty">
                <p className="muted">{currentSlide.title}: no HTML generated</p>
              </div>
            )}
          </div>
        </div>

        <button
          type="button"
          onClick={goToNext}
          aria-label="Next slide"
          className="icon-btn nav"
        >
          <FontAwesomeIcon icon={faChevronRight} />
        </button>
      </div>

      <nav className="dots" aria-label="slides">
        {slides.map((s, i) => (
          <button
            key={s.slideType}
            type="button"
            className={i === currentIndex ? 'dot on' : 'dot'}
            onClick={() => setCurrentIndex(i)}
            aria-label={s.title}
            aria-current={i === currentIndex}
          />
        ))}
      </nav>
    </main>
  );
}

export default SlideCarousel;
