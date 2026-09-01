import { useEffect, useMemo, useState, useRef, TouchEvent } from 'react';
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

const SWIPE_THRESHOLD = 50;

const SHARE_SLIDE: WrappedSlide = {
    slideType: 'Share',
    title: "Share",
    stats: null,
    generatedAt: null,
    html: "<div class=\"ca-slide\" style=\"width:400px;height:700px;background:linear-gradient(180deg,#0a0e2a 0%,#151a3d 45%,#2a1f4d 70%,#3d2456 100%);display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;position:relative;overflow:hidden;\">\n\n<style>\n  .ca-slide .stars-big {\n    position:absolute;top:0;left:0;width:1px;height:1px;background:transparent;\n    box-shadow:23px 45px #fff,88px 12px #fff,142px 78px #fff,201px 30px #fff,267px 95px #fff,44px 140px #fff,310px 55px #fff,355px 110px #fff,15px 200px #fff,120px 175px #fff,250px 160px #fff,340px 200px #fff,70px 240px #fff,190px 220px #fff,10px 280px #fff,300px 260px #fff;\n    animation:ca-twinkle 3s ease-in-out infinite alternate;\n  }\n  .ca-slide .stars-small {\n    position:absolute;top:0;left:0;width:1px;height:1px;background:transparent;\n    box-shadow:60px 30px #8b93c9,100px 90px #8b93c9,180px 20px #8b93c9,220px 110px #8b93c9,30px 160px #8b93c9,160px 140px #8b93c9,280px 40px #8b93c9,370px 150px #8b93c9,90px 250px #8b93c9,230px 270px #8b93c9,330px 100px #8b93c9,5px 100px #8b93c9;\n    animation:ca-twinkle 2.2s ease-in-out infinite alternate-reverse;\n  }\n  @keyframes ca-twinkle {\n    from { opacity:0.3; }\n    to { opacity:1; }\n  }\n  .ca-slide .horizon {\n    position:absolute;bottom:0;left:0;width:100%;height:180px;\n    background:linear-gradient(180deg,rgba(61,36,86,0) 0%,#1a0f2e 100%);\n  }\n  .ca-slide .share-btn {\n    padding:14px 32px;\n    background:transparent;\n    color:#FFFFFF;\n    font-weight:700;\n    font-size:15px;\n    border:1px solid #FFFFFF;\n    border-radius:999px;\n    cursor:pointer;\n    box-shadow:0 0 24px rgba(48,255,120,0.25);\n    position:relative;\n    z-index:2;\n  }\n  .ca-slide .share-btn:active {\n    transform:scale(0.97);\n  }\n  .ca-slide .toast {\n    position:absolute;\n    bottom:40px;\n    font-size:13px;\n    color:#c9b8e8;\n    opacity:0;\n    transition:opacity 0.2s;\n    z-index:2;\n  }\n  .ca-slide .toast.show {\n    opacity:1;\n  }\n  .ca-slide .particle {\n    position:absolute;\n    width:6px;height:6px;\n    border-radius:50%;\n    left:200px;top:350px;\n    pointer-events:none;\n    animation:ca-burst 900ms ease-out forwards;\n  }\n  @keyframes ca-burst {\n    to {\n      transform:translate(var(--dx),var(--dy)) scale(0.2);\n      opacity:0;\n    }\n  }\n</style>\n\n  <div class=\"stars-big\"></div>\n  <div class=\"stars-small\"></div>\n  <div class=\"horizon\"></div>\n\n  <button class=\"share-btn\" onclick=\"navigator.clipboard.writeText(window.location.href).then(()=>{var t=this.parentElement.querySelector('.toast');t.classList.add('show');setTimeout(()=>t.classList.remove('show'),1500);});var s=this.parentElement;var colors=['#FFFFFF','#ffd23f','#ff6b6b','#4ecdc4','#a78bfa'];for(var i=0;i<28;i++){var p=document.createElement('div');p.className='particle';var ang=(Math.PI*2*i)/28+Math.random()*0.3;var dist=150+Math.random()*150;p.style.setProperty('--dx',(Math.cos(ang)*dist)+'px');p.style.setProperty('--dy',(Math.sin(ang)*dist)+'px');p.style.background=colors[i%colors.length];p.style.boxShadow='0 0 6px '+colors[i%colors.length];s.appendChild(p);setTimeout(function(el){el.remove();},900,p);}\">\n    Share GitHub Wrapped\n  </button>\n  <div class=\"toast\">Copied!</div>\n\n</div>"
}

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
 
  const scale = useSlideScale();

  const slides = useMemo(() => [...data.slides, SHARE_SLIDE], [data.slides]);
  
  const touchStartX = useRef<number | null>(null);
  const touchEndX = useRef<number | null>(null);

  const goToPrevious = () =>
    setCurrentIndex((prev) => (prev === 0 ? slides.length - 1 : prev - 1));

  const goToNext = () =>
    setCurrentIndex((prev) => (prev === slides.length - 1 ? 0 : prev + 1));

    const handleTouchStart = (e: TouchEvent) => {
    touchEndX.current = null;
    touchStartX.current = e.targetTouches[0].clientX;
  };

  const handleTouchMove = (e: TouchEvent) => {
    touchEndX.current = e.targetTouches[0].clientX;
  };

  const handleTouchEnd = () => {
    if (touchStartX.current === null || touchEndX.current === null) return;

    const distance = touchStartX.current - touchEndX.current;

    if (distance > SWIPE_THRESHOLD) {
      goToNext(); // swiped left -> next slide
    } else if (distance < -SWIPE_THRESHOLD) {
      goToPrevious(); // swiped right -> previous slide
    }

    touchStartX.current = null;
    touchEndX.current = null;
  };

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
          className="hidden sm:block icon-btn nav"
        >
          <FontAwesomeIcon icon={faChevronLeft} />
        </button>

        <div
          className="slide-frame"
          onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
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
          className="hidden sm:block icon-btn nav"
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
