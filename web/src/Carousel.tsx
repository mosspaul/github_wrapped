import { useState } from 'react';
import { faChevronLeft, faChevronRight } from '@fortawesome/free-solid-svg-icons';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { WrappedSlide } from './api';

function SlideCarousel({ data }: { data: { slides: Array<WrappedSlide> } }) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const slides = data.slides;

  const goToPrevious = () => {
    setCurrentIndex((prev) => (prev === 0 ? slides.length - 1 : prev - 1));
  };

  const goToNext = () => {
    setCurrentIndex((prev) => (prev === slides.length - 1 ? 0 : prev + 1));
  };

  if (slides.length === 0) {
    return null;
  }

  const currentSlide = slides[currentIndex];

  return (
    <div className='flex items-center justify-center gap-1'>
      <button
        type="button"
        onClick={goToPrevious}
        aria-label="Previous slide"
        className="bg-transparent! border-none! cursor-pointer p-2 text-2xl"
      >
        <FontAwesomeIcon icon={faChevronLeft} />
      </button>

      <div className='flex justify-center items-center'>
        {currentSlide.html ? (
          // Safe only because this HTML comes from our own Bedrock call
          // with our own prompt. Never render user-supplied HTML here.
          <div
            key={currentSlide.slideType}
            className="slide-preview"
            dangerouslySetInnerHTML={{ __html: currentSlide.html }}
          />
        ) : (
          <div key={currentSlide.slideType} className="slide-preview">
            <p className="muted" style={{ padding: '1rem' }}>
              {currentSlide.title}: no HTML generated
            </p>
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={goToNext}
        aria-label="Next slide"
        className="bg-transparent! border-none! cursor-pointer p-2 text-2xl"
      >
        <FontAwesomeIcon icon={faChevronRight} />
      </button>
    </div>
  );
}

export default SlideCarousel;