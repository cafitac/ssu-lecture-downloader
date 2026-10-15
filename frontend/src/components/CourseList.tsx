import type { CourseSummary, TermSummary } from '../lib/types';

interface Props {
  courses: CourseSummary[];
  terms: TermSummary[];
  selectedTermId: number | null;
  selectedCourse: CourseSummary | null;
  /** Disables term/course changes while the list loads or a job is running. */
  disabled: boolean;
  onTermChange: (termId: number | null) => void;
  onLoadTerm: () => void;
  onSelectCourse: (course: CourseSummary) => void;
}

export function CourseList({ courses, terms, selectedTermId, selectedCourse, disabled, onTermChange, onLoadTerm, onSelectCourse }: Props) {
  return (
    <aside className="panel course-panel">
      <div className="panel-header">
        <h2>현재 학기 과목</h2>
        <span>{courses.length}개</span>
      </div>
      <div className="term-selector">
        <label htmlFor="term-select">학기 선택</label>
        <select id="term-select" value={selectedTermId ?? ''} onChange={(event) => onTermChange(event.target.value ? Number(event.target.value) : null)} disabled={disabled}>
          <option value="">현재 학기 자동</option>
          {terms.map((term) => (
            <option key={term.term_id} value={term.term_id}>
              {term.is_current ? '현재 ' : '과거 '}학기 {term.term_id} · 과목 {term.course_count}개
            </option>
          ))}
        </select>
        <button onClick={onLoadTerm} disabled={disabled}>선택한 학기 불러오기</button>
      </div>
      <p className="hint">과거 학기는 학기 선택에서 명시적으로 불러옵니다.</p>
      <div className="course-list">
        {courses.map((course) => (
          <button
            key={course.id}
            className={`course-card ${selectedCourse?.id === course.id ? 'selected' : ''}`}
            onClick={() => onSelectCourse(course)}
            disabled={disabled}
          >
            <strong>{course.name}</strong>
            <span>modules {course.module_count} · lectures {course.lecture_count}</span>
            <span>미완료 {course.uncompleted_lecture_count}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}
